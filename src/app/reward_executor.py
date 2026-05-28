from __future__ import annotations

from typing import Any

from src.app.treat_reward import build_treat_reward_decision


class RewardExecutor:
    def __init__(self, api: Any, daily_limit: int = 8):
        self.api = api
        self.daily_limit = daily_limit
        self.dispensed_today = 0

    def maybe_reward(self, session_summary: dict[str, Any]) -> dict[str, Any]:
        summary = dict(session_summary)
        if summary.get("outcome") == "lost_target":
            summary["lost_target"] = True
        if summary.get("outcome") == "caught":
            summary["catch_success"] = True
        decision = build_treat_reward_decision(
            summary,
            {
                "dispensed_today": self.dispensed_today,
                "daily_limit": self.daily_limit,
                "remaining_treats": int(summary.get("treats_remaining", 10) or 10),
            },
        )
        if not decision["allowed"]:
            return {"dispensed": False, "decision": decision}
        response = self.api.dispense_treat(grams=float(decision["recommended_amount"]), reason=decision["reason"])
        self.dispensed_today += 1
        return {"dispensed": True, "decision": decision, "response": response}
