from __future__ import annotations

from typing import Any


POOR_OUTCOMES = {"lost_target", "error", "no_target"}


def build_interaction_strategy(summary: dict[str, Any]) -> dict[str, Any]:
    activity = summary.get("activity", {}) if isinstance(summary.get("activity", {}), dict) else {}
    engagement_score = activity.get("engagement_score", 0)
    if summary.get("error") or summary.get("final_state") == "error" or not summary.get("healthy", True):
        return _strategy(
            "safe_pause",
            "high",
            "本次互动出现异常，Hunter 应先保证安全。",
            "保守暂停，检查感知和动作链路后再继续。",
        )
    if summary.get("lost_target"):
        return _strategy(
            "safe_pause",
            "high",
            "目标曾经出现但中途丢失，继续追逐会增加风险。",
            "保守暂停，等待目标重新稳定出现。",
        )
    if summary.get("reached_stop_distance"):
        confidence = "high" if engagement_score >= 70 else "medium"
        reason = "已经安全靠近目标，互动质量较高。" if engagement_score >= 70 else "已经安全靠近目标，但还需要继续观察互动兴趣。"
        return _strategy(
            "continue_engagement",
            confidence,
            reason,
            "保持低强度互动，并观察猫是否继续感兴趣。",
        )
    if engagement_score >= 70:
        return _strategy(
            "continue_engagement",
            "high",
            "互动质量较高，可以继续当前玩法。",
            "保持低强度互动，并观察猫是否继续感兴趣。",
        )
    if summary.get("target_seen"):
        return _strategy(
            "continue_engagement",
            "medium",
            "已经看到目标，但互动还没有形成稳定闭环。",
            "降低速度继续观察，确认目标稳定后再靠近。",
        )
    return _strategy(
        "search_again",
        "medium",
        "本次没有看到稳定目标，当前更适合继续搜索。",
        "重新搜索目标，并保持待机安全距离。",
    )


def build_suite_strategy(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = []
    for artifact in artifacts:
        report = artifact.get("report", {}) if isinstance(artifact.get("report", {}), dict) else {}
        outcome = report.get("outcome")
        if outcome:
            outcomes.append(outcome)
    if len(outcomes) >= 3 and all(outcome in POOR_OUTCOMES for outcome in outcomes[-3:]):
        return _strategy(
            "recovery_check",
            "high",
            "最近连续出现低质量结果，需要先恢复稳定性。",
            "先运行保守场景，确认感知稳定后再提高互动强度。",
        )
    latest_summary = artifacts[-1].get("summary", {}) if artifacts else {}
    if not isinstance(latest_summary, dict):
        latest_summary = {}
    return build_interaction_strategy(latest_summary)


def _strategy(decision: str, confidence: str, reason: str, next_action: str) -> dict[str, Any]:
    return {
        "decision": decision,
        "confidence": confidence,
        "reason": reason,
        "next_action": next_action,
    }
