"""Replay labeled cases through one review command and report error counts."""

import argparse
import json
import shlex
import time
from pathlib import Path

from macv import load_cases, review_one


def compare(cases, command, root, timeout):
    counts = {"correct": 0, "false_pass": 0, "false_fail": 0, "uncertain": 0}
    models = set()
    tokens = {"input_tokens": 0, "output_tokens": 0}
    started = time.perf_counter()
    for case in cases:
        result = review_one({"id": "comparison", "command": command}, case,
                            case["output"], root, timeout)
        if result["status"] == "uncertain":
            counts["uncertain"] += 1
        elif result["passed"] == case["passed"]:
            counts["correct"] += 1
        elif result["passed"]:
            counts["false_pass"] += 1
        else:
            counts["false_fail"] += 1
        if result.get("model"):
            models.add(result["model"])
        for key in tokens:
            tokens[key] += (result.get("usage") or {}).get(key) or 0
    return {"cases": len(cases), **counts, "seconds": round(time.perf_counter() - started, 3),
            "models": sorted(models), "usage": tokens}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="examples/calibration.jsonl")
    parser.add_argument("--command", default="python3 examples/mock_agent.py")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    print(json.dumps(compare(load_cases(root / args.data), shlex.split(args.command),
                             root, args.timeout), indent=2))
