from __future__ import annotations

from typing import Any


DEFAULT_DAILY_LIMIT = 12
DEFAULT_REMAINING_TREATS = 50


def build_treat_reward_decision(
    session_summary: dict[str, Any],
    reward_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = reward_state or {}
    daily_limit = _int(state.get("daily_limit"), DEFAULT_DAILY_LIMIT)
    dispensed_today = _int(state.get("dispensed_today"), 0)
    remaining_treats = _int(state.get("remaining_treats"), DEFAULT_REMAINING_TREATS)
    catch_success = _catch_success(session_summary)
    blocked_reasons = _blocked_reasons(catch_success, daily_limit, dispensed_today, remaining_treats, session_summary)
    allowed = not blocked_reasons
    amount = _reward_amount(session_summary) if allowed else 0
    return {
        "capability": "treat_reward_policy",
        "allowed": allowed,
        "catch_success": catch_success,
        "recommended_amount": amount,
        "drop_target_cm": 20,
        "daily_limit": daily_limit,
        "dispensed_today": dispensed_today,
        "remaining_treats": remaining_treats,
        "blocked_reasons": blocked_reasons,
        "action": "dispense_treat" if allowed else "skip_reward",
        "reason": _reason(allowed, blocked_reasons, amount),
    }


def build_treat_reward_preview() -> dict[str, Any]:
    success_summary = {
        "reached_stop_distance": True,
        "healthy": True,
        "lost_target": False,
        "final_state": "at_stop_distance",
        "activity": {"engagement_score": 80},
    }
    limited_summary = dict(success_summary)
    return {
        "capability": "treat_reward_policy",
        "cases": {
            "successful_catch": build_treat_reward_decision(success_summary, {"dispensed_today": 3, "daily_limit": 12}),
            "daily_limit_reached": build_treat_reward_decision(limited_summary, {"dispensed_today": 12, "daily_limit": 12}),
            "lost_target": build_treat_reward_decision({"lost_target": True, "healthy": True}, {"dispensed_today": 0}),
            "empty_dispenser": build_treat_reward_decision(success_summary, {"remaining_treats": 0}),
        },
    }


def _catch_success(summary: dict[str, Any]) -> bool:
    return bool(
        summary.get("reached_stop_distance")
        or summary.get("final_state") == "at_stop_distance"
        or summary.get("catch_success")
    )


def _blocked_reasons(
    catch_success: bool,
    daily_limit: int,
    dispensed_today: int,
    remaining_treats: int,
    summary: dict[str, Any],
) -> list[str]:
    reasons = []
    if not catch_success:
        reasons.append("no_catch_success")
    if not summary.get("healthy", True) or summary.get("error"):
        reasons.append("session_unhealthy")
    if summary.get("lost_target"):
        reasons.append("target_lost")
    if dispensed_today >= daily_limit:
        reasons.append("daily_limit_reached")
    if remaining_treats <= 0:
        reasons.append("empty_dispenser")
    return reasons


def _reward_amount(summary: dict[str, Any]) -> int:
    activity = summary.get("activity", {}) if isinstance(summary.get("activity", {}), dict) else {}
    engagement = activity.get("engagement_score", 0)
    if isinstance(engagement, int | float) and engagement >= 80:
        return 2
    return 1


def _reason(allowed: bool, blocked_reasons: list[str], amount: int) -> str:
    if allowed:
        return f"检测到有效扑抓/靠近成功，建议奖励 {amount} 粒零食。"
    return "不投喂：" + "、".join(blocked_reasons)


def _int(value: Any, default: int) -> int:
    return int(value) if isinstance(value, int) else default
