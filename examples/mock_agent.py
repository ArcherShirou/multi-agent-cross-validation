"""Deterministic adapter for exercising the protocol without a model key."""

import json
import sys


def respond(request):
    action = request["action"]
    if action == "generate":
        output = request["task"]
        if "VERIFY_IDENTITY" in request["skill"]:
            output += " Verify identity first."
        if "NEXT_STEP" in request["agent"]:
            output += " Send a reset link as the next step."
        return {"output": output}
    if action == "review":
        missing = [term for term in request["rubric"]["required_terms"]
                   if term.lower() not in request["output"].lower()]
        return {"passed": not missing, "reason": "missing: " + ", ".join(missing) if missing else "meets rubric"}
    if action == "propose":
        reasons = " ".join(" ".join(card["reasons"]) for card in request["failures"])
        skill = request["skill"]
        agent = request["agent"]
        if "Verify identity" in reasons and "VERIFY_IDENTITY" not in skill:
            skill += "\nVERIFY_IDENTITY: Verify identity before account recovery.\n"
            return {"target": "skill", "skill": skill, "agent": agent}
        if "reset link" in reasons and "NEXT_STEP" not in agent:
            agent += "\nNEXT_STEP: Give the user a concrete next step.\n"
            return {"target": "agent", "skill": skill, "agent": agent}
        return {"target": "skill", "skill": skill + "\nNo further change.\n", "agent": agent}
    raise ValueError(f"Unknown action: {action}")


if __name__ == "__main__":
    print(json.dumps(respond(json.load(sys.stdin))))
