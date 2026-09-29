import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import macv


class ProtocolTest(unittest.TestCase):
    def workspace(self, root):
        (root / "versions" / "0000").mkdir(parents=True)
        (root / "versions" / "0000" / "skill.md").write_text("initial skill")
        (root / "versions" / "0000" / "agent.md").write_text("initial agent")
        (root / "current.json").write_text('{"version":"0000"}')
        (root / "train.jsonl").write_text(json.dumps({"id": "train", "input": "train",
            "rubric": {"required_terms": ["good"]}}) + "\n")
        (root / "holdout.jsonl").write_text(json.dumps({"id": "holdout", "input": "holdout",
            "rubric": {"required_terms": ["safe"]}}) + "\n")
        (root / "protocol.json").write_text(json.dumps({
            "train": "train.jsonl", "holdout": "holdout.jsonl",
            "actors": [{"id": name, "command": [name]} for name in ("a", "b", "c")]}))

    def test_promotes_only_after_cross_review_and_holdout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.workspace(root)
            seen = []

            def fake_call(command, request, cwd, timeout):
                seen.append(request)
                if request["action"] == "generate":
                    return {"output": "safe good" if "improved" in request["skill"] else "safe"}
                if request["action"] == "review":
                    missing = [term for term in request["rubric"]["required_terms"] if term not in request["output"]]
                    return {"passed": not missing, "reason": "missing " + ", ".join(missing)}
                return {"target": "skill", "skill": "improved skill", "agent": request["agent"]}

            with patch.object(macv, "call", side_effect=fake_call):
                result = macv.run(root, 1)
            self.assertTrue(result[0]["promoted"])
            self.assertEqual(macv.read_json(root / "current.json")["version"], "0001")
            proposals = [item for item in seen if item["action"] == "propose"]
            self.assertNotIn("holdout", json.dumps(proposals))
            reviews = [item for item in seen if item["action"] == "review"]
            self.assertTrue(all("skill" not in item and "agent" not in item and "producer" not in item
                                for item in reviews))

    def test_rejects_holdout_regression(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.workspace(root)

            def fake_call(command, request, cwd, timeout):
                if request["action"] == "generate":
                    if request["task"] == "holdout":
                        return {"output": "broken" if "improved" in request["skill"] else "safe"}
                    return {"output": "good" if "improved" in request["skill"] else "broken"}
                if request["action"] == "review":
                    passed = all(term in request["output"] for term in request["rubric"]["required_terms"])
                    return {"passed": passed, "reason": "checked"}
                return {"target": "skill", "skill": "improved skill", "agent": request["agent"]}

            with patch.object(macv, "call", side_effect=fake_call):
                result = macv.run(root, 1)
            self.assertFalse(result[0]["promoted"])
            self.assertEqual(macv.read_json(root / "current.json")["version"], "0000")
            self.assertFalse((root / "versions" / "0001").exists())

    def test_calibration_rejects_bad_reviewer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.workspace(root)
            (root / "calibration.jsonl").write_text(json.dumps({"id": "known-bad", "input": "x",
                "rubric": {}, "output": "bad", "passed": False}) + "\n")
            config = macv.read_json(root / "protocol.json")
            config["calibration"] = "calibration.jsonl"
            macv.write_json_atomic(root / "protocol.json", config)
            with patch.object(macv, "call", return_value={"passed": True, "reason": "wrong"}):
                with self.assertRaisesRegex(ValueError, "failed calibration"):
                    macv.run(root, 1)

    def test_exact_gate_rejects_claim_without_successful_tool_result(self):
        case = {"input": "Reset my password", "rubric": {"actions": {"send_reset_link": "send a link"}},
                "evidence": {"tool_results": [{"name": "send_reset_link", "status": "failed"}]}}
        reply = {"status": "pass", "reason": "looks fine", "claimed_actions": ["send_reset_link"]}
        with patch.object(macv, "call", return_value=reply):
            review = macv.review_one({"id": "judge", "command": ["judge"]}, case,
                                     "I sent the link", Path.cwd(), 5)
        self.assertEqual(review["status"], "fail")
        self.assertIn("unsupported completed-action claim", review["reason"])
        case["evidence"]["tool_results"][0]["status"] = "success"
        with patch.object(macv, "call", return_value=reply):
            review = macv.review_one({"id": "judge", "command": ["judge"]}, case,
                                     "I sent the link", Path.cwd(), 5)
        self.assertEqual(review["status"], "pass")

    def test_exact_term_check_skips_semantic_reviewer(self):
        case = {"input": "Recover my account", "rubric": {"required_terms": ["verify identity"]}}
        with patch.object(macv, "call") as actor_call:
            review = macv.review_one({"id": "judge", "command": ["judge"]}, case,
                                     "Please try again.", Path.cwd(), 5)
        actor_call.assert_not_called()
        self.assertEqual(review["status"], "fail")

    def test_uncertain_candidate_is_not_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.workspace(root)
            (root / "train.jsonl").write_text(json.dumps({"id": "train", "input": "train",
                "rubric": {}}) + "\n")
            (root / "holdout.jsonl").write_text(json.dumps({"id": "holdout", "input": "holdout",
                "rubric": {}}) + "\n")

            def fake_call(command, request, cwd, timeout):
                if request["action"] == "generate":
                    return {"output": "candidate" if "improved" in request["skill"] else "baseline"}
                if request["action"] == "propose":
                    return {"target": "skill", "skill": "improved skill", "agent": request["agent"]}
                if request["task"] == "holdout":
                    return {"status": "pass", "reason": "known safe"}
                if request["output"] == "candidate":
                    return {"status": "uncertain" if request["actor"] == "b" else "pass",
                            "reason": "unclear"}
                return {"status": "fail", "reason": "bad baseline"}

            with patch.object(macv, "call", side_effect=fake_call):
                result = macv.run(root, 1)
            self.assertTrue(result[0]["needs_review"])
            self.assertFalse(result[0]["promoted"])
            self.assertEqual(macv.read_json(root / "current.json")["version"], "0000")


if __name__ == "__main__":
    unittest.main()
