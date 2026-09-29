import unittest
from types import SimpleNamespace

from examples.jev_reviewer import review, verdict


class JevReviewerTest(unittest.TestCase):
    def test_probability_routing(self):
        self.assertEqual(verdict({"grounded": 0.98, "addresses_request": 0.91,
                                  "claim_send_reset_link": 0.02})["status"], "pass")
        self.assertEqual(verdict({"grounded": 0.95, "addresses_request": 0.55})["status"], "uncertain")
        self.assertEqual(verdict({"grounded": 0.10, "addresses_request": 0.95,
                                  "claim_send_reset_link": 0.93})["claimed_actions"], ["send_reset_link"])
        with self.assertRaises(ValueError):
            verdict({"grounded": float("nan"), "addresses_request": 0.9})

    def test_one_request_contains_evidence_and_all_questions(self):
        class Client:
            def system_one(self, *, state, questions):
                self.state, self.questions = state, questions
                return SimpleNamespace(model="fake-jev", usage=SimpleNamespace(input_tokens=10, output_tokens=3), nouls={
                    key: SimpleNamespace(noul=0.95 if key != "claim_send_reset_link" else 0.05)
                    for key in questions})

        client = Client()
        request = {"task": "Reset my password", "output": "Use the recovery page.",
                   "rubric": {"actions": {"send_reset_link": "send a reset link"}},
                   "evidence": {"policy": "Verify identity first", "tool_results": []}}
        result = review(request, client, lambda **kwargs: kwargs)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(set(client.questions), {"grounded", "addresses_request", "claim_send_reset_link"})
        self.assertEqual(client.state["policy"], "Verify identity first")
        self.assertNotIn("rubric", client.state)
