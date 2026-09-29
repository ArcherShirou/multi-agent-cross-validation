"""Small, model-agnostic cross-validation loop for evolving agent instructions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
        temporary = Path(f.name)
    os.replace(temporary, path)


def call(command: list[str], payload: dict, cwd: Path, timeout: int) -> dict:
    if not command or not all(isinstance(part, str) and part for part in command):
        raise ValueError("Each actor needs a nonempty command array")
    result = subprocess.run(
        command,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        cwd=cwd,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Actor command failed ({result.returncode}): {result.stderr[:1000]}")
    if len(result.stdout) > 100_000:
        raise ValueError("Actor response is too large")
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Actor did not return JSON") from exc
    if not isinstance(response, dict):
        raise ValueError("Actor response must be a JSON object")
    return response


def load_cases(path: Path) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not cases or any(not isinstance(case, dict) or not case.get("id") for case in cases):
        raise ValueError(f"Expected nonempty JSONL cases with IDs: {path}")
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate case ID in {path}")
    return cases


def review_one(reviewer: dict, case: dict, output: str, cwd: Path, timeout: int) -> dict:
    rubric = case["rubric"]
    evidence = case.get("evidence", {})
    missing = [term for term in rubric.get("required_terms", []) if term.lower() not in output.lower()]
    if missing:
        return {"reviewer": reviewer["id"], "status": "fail", "passed": False,
                "reason": "; ".join(f"missing required term: {term}" for term in missing)[:500]}
    judged = call(reviewer.get("review_command", reviewer["command"]), {
        "action": "review", "actor": reviewer["id"], "task": case["input"],
        "rubric": rubric, "output": output, "evidence": evidence,
    }, cwd, timeout)
    status = judged.get("status")
    if status is None and type(judged.get("passed")) is bool:
        status = "pass" if judged["passed"] else "fail"
    if status not in ("pass", "fail", "uncertain") or not isinstance(judged.get("reason"), str):
        raise ValueError("Reviewer must return status (pass/fail/uncertain) and reason")
    claims = judged.get("claimed_actions", [])
    actions = rubric.get("actions", {})
    if not isinstance(claims, list) or any(not isinstance(item, str) or item not in actions for item in claims):
        raise ValueError("Reviewer returned an unknown claimed action")
    if actions and "claimed_actions" not in judged:
        raise ValueError("Reviewer must report claimed_actions when rubric defines actions")
    unsupported = []
    for claim in set(claims):
        expected = actions[claim]
        if not isinstance(expected, dict) or not all(expected.get(key) for key in ("name", "subject", "target")):
            raise ValueError(f"Action {claim} needs name, subject, and target")
        run_id = evidence.get("run_id")
        matched = isinstance(run_id, str) and bool(run_id) and any(
            isinstance(event, dict) and event.get("status") == "success"
            and isinstance(event.get("call_id"), str) and bool(event["call_id"])
            and event.get("run_id") == run_id
            and all(event.get(key) == expected[key] for key in ("name", "subject", "target"))
            for event in evidence.get("tool_results", []))
        if not matched:
            unsupported.append(claim)
    unsupported.sort()
    reasons = [judged["reason"]] if status != "pass" else []
    reasons += [f"unsupported completed-action claim: {action}" for action in unsupported]
    if unsupported:
        status = "fail"
    result = {"reviewer": reviewer["id"], "status": status, "passed": status == "pass",
              "reason": "; ".join(reason for reason in reasons if reason)[:500]}
    for key in ("model", "usage"):
        if key in judged:
            result[key] = judged[key]
    return result


def evaluate(cases: list[dict], actors: list[dict], skill: str, agent: str, cwd: Path, timeout: int):
    records = []
    for case in cases:
        for producer in actors:
            made = call(producer["command"], {
                "action": "generate", "actor": producer["id"], "task": case["input"],
                "skill": skill, "agent": agent,
            }, cwd, timeout)
            output = made.get("output")
            if not isinstance(output, str) or len(output) > 20_000:
                raise ValueError("Generator must return an output string of at most 20,000 characters")
            reviews = []
            for reviewer in actors:
                if reviewer["id"] == producer["id"]:
                    continue
                # Reviewers see evidence, but not producer identity or instructions.
                reviews.append(review_one(reviewer, case, output, cwd, timeout))
            # All independent reviewers must pass. A single veto prevents promotion for this case.
            passed = all(review["passed"] for review in reviews)
            records.append({"case": case["id"], "producer": producer["id"],
                            "passed": passed, "output": output, "reviews": reviews})
    return records


def score(records: list[dict]) -> float:
    votes = [review["passed"] for record in records for review in record["reviews"]]
    return sum(votes) / len(votes)


def failure_cards(records: list[dict]) -> list[dict]:
    cards = []
    seen = set()
    for record in records:
        if record["passed"]:
            continue
        card = {"case": record["case"], "output": record["output"],
                "reasons": sorted({review["reason"] for review in record["reviews"]
                                   if not review["passed"]})}
        identity = (card["case"], card["output"], tuple(card["reasons"]))
        if identity not in seen:
            cards.append(card)
            seen.add(identity)
    return cards


def calibrate(actors: list[dict], cases: list[dict], cwd: Path, timeout: int):
    for actor in actors:
        for case in cases:
            judgment = review_one(actor, case, case["output"], cwd, timeout)
            expected = "pass" if case["passed"] else "fail"
            if judgment["status"] != expected:
                raise ValueError(f"Reviewer {actor['id']} failed calibration case {case['id']}")


def run(root: Path, rounds: int) -> list[dict]:
    config = read_json(root / "protocol.json")
    actors = config["actors"]
    if len(actors) < 3 or len({a["id"] for a in actors}) != len(actors):
        raise ValueError("At least three uniquely named actors are required")
    timeout = int(config.get("timeout_seconds", 30))
    if timeout < 1 or timeout > 300 or rounds < 1:
        raise ValueError("Timeout must be 1–300 seconds and rounds must be positive")
    train = load_cases(root / config["train"])
    validation = load_cases(root / config["validation"])
    if {case["id"] for case in train} & {case["id"] for case in validation}:
        raise ValueError("Train and validation case IDs must be disjoint")
    if config.get("calibration"):
        calibrate(actors, load_cases(root / config["calibration"]), root, timeout)
    current = read_json(root / "current.json")["version"]
    history = []
    for round_index in range(rounds):
        version_dir = root / "versions" / current
        skill = (version_dir / "skill.md").read_text(encoding="utf-8")
        agent = (version_dir / "agent.md").read_text(encoding="utf-8")
        baseline_train = evaluate(train, actors, skill, agent, root, timeout)
        baseline_validation = evaluate(validation, actors, skill, agent, root, timeout)
        proposer = actors[round_index % len(actors)]
        proposal = call(proposer["command"], {
            "action": "propose", "actor": proposer["id"],
            "skill": skill, "agent": agent, "failures": failure_cards(baseline_train),
            "train_score": score(baseline_train),
        }, root, timeout)
        new_skill, new_agent = proposal.get("skill"), proposal.get("agent")
        target = proposal.get("target")
        if not all(isinstance(value, str) and 0 < len(value) <= 16_000
                   for value in (new_skill, new_agent)):
            raise ValueError("Proposal must contain nonempty skill and agent strings up to 16,000 characters")
        if target not in ("skill", "agent") or (new_skill != skill) == (new_agent != agent):
            raise ValueError("Proposal must change exactly one target: skill or agent")
        if target == "skill" and new_agent != agent or target == "agent" and new_skill != skill:
            raise ValueError("Proposal target does not match changed instruction file")
        candidate_train = evaluate(train, actors, new_skill, new_agent, root, timeout)
        candidate_validation = evaluate(validation, actors, new_skill, new_agent, root, timeout)
        needs_review = any(review["status"] == "uncertain" for record in candidate_train + candidate_validation
                           for review in record["reviews"])
        train_gain = score(candidate_train) > score(baseline_train)
        no_validation_regression = score(candidate_validation) >= score(baseline_validation) and all(
            not old["passed"] or new["passed"] for old, new in zip(baseline_validation, candidate_validation))
        promoted = (new_skill != skill or new_agent != agent) and train_gain and no_validation_regression and not needs_review
        summary = {
            "round": round_index + 1, "from": current, "promoted": promoted,
            "target": target,
            "needs_review": needs_review,
            "train_before": score(baseline_train), "train_after": score(candidate_train),
            "validation_before": score(baseline_validation), "validation_after": score(candidate_validation),
            "failure_cards": failure_cards(baseline_train),
        }
        if promoted:
            next_version = f"{int(current) + 1:04d}"
            destination = root / "versions" / next_version
            if destination.exists():
                raise FileExistsError(f"Version already exists: {destination}")
            destination.mkdir(parents=True)
            (destination / "skill.md").write_text(new_skill, encoding="utf-8")
            (destination / "agent.md").write_text(new_agent, encoding="utf-8")
            write_json_atomic(destination / "manifest.json", {key: value for key, value in summary.items()
                                                              if key != "failure_cards"})
            write_json_atomic(root / "current.json", {"version": next_version})
            current = next_version
            summary["to"] = current
        history.append(summary)
        if not promoted:
            break
    write_json_atomic(root / "last_run.json", {"rounds": history})
    return history


def final_test(root: Path, dataset: Path) -> dict:
    root, dataset = root.resolve(), dataset.resolve()
    if dataset == root or root in dataset.parents:
        raise ValueError("Final test dataset must be outside the repository")
    result_path = root / "final_test_result.json"
    if result_path.exists():
        raise FileExistsError("Final test already ran here; use a fresh sealed set for another release")
    config = read_json(root / "protocol.json")
    test_cases = load_cases(dataset)
    development_ids = {case["id"] for split in ("train", "validation")
                       for case in load_cases(root / config[split])}
    if development_ids & {case["id"] for case in test_cases}:
        raise ValueError("Final test case IDs overlap development cases")
    actors = config["actors"]
    if len(actors) < 3 or len({a["id"] for a in actors}) != len(actors):
        raise ValueError("At least three uniquely named actors are required")
    timeout = int(config.get("timeout_seconds", 30))
    if timeout < 1 or timeout > 300:
        raise ValueError("Timeout must be 1–300 seconds")
    if config.get("calibration"):
        calibrate(actors, load_cases(root / config["calibration"]), root, timeout)
    version = read_json(root / "current.json")["version"]
    version_dir = root / "versions" / version
    records = evaluate(test_cases, actors,
                       (version_dir / "skill.md").read_text(encoding="utf-8"),
                       (version_dir / "agent.md").read_text(encoding="utf-8"), root, timeout)
    result = {"version": version, "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
              "cases": len(test_cases), "vote_score": score(records),
              "passed_cases": sum(record["passed"] for record in records), "case_runs": len(records),
              "needs_review": any(review["status"] == "uncertain" for record in records
                                  for review in record["reviews"])}
    write_json_atomic(result_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--final-test", type=Path, help="Run the selected version once on an external test set")
    args = parser.parse_args()
    try:
        result = final_test(args.root, args.final_test) if args.final_test else run(args.root.resolve(), args.rounds)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
