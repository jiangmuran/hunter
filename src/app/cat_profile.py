from __future__ import annotations

from typing import Any


DEFAULT_ARM = "wand_slow"


def build_cat_profile(
    artifacts: list[dict[str, Any]],
    memory_preferences: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    preferences = memory_preferences or []
    preferred_arm = preferences[0].get("arm", DEFAULT_ARM) if preferences and isinstance(preferences[0], dict) else DEFAULT_ARM
    scores = [_engagement_score(artifact) for artifact in artifacts]
    scores = [score for score in scores if score is not None]
    outcomes = [_outcome(artifact) for artifact in artifacts]
    outcomes = [outcome for outcome in outcomes if outcome]
    risk_flags = sorted({outcome for outcome in outcomes if outcome in {"lost_target", "error"}})

    if not artifacts:
        return {
            "engagement_level": "unknown",
            "preferred_arm": preferred_arm,
            "play_style": "待观察型",
            "risk_flags": [],
            "summary": "还没有足够历史记录，先使用慢速默认互动观察猫咪反应。",
        }

    average_score = round(sum(scores) / len(scores)) if scores else 0
    engagement_level = _engagement_level(average_score)
    play_style = _play_style(engagement_level, risk_flags)
    summary = _summary(preferred_arm, engagement_level, risk_flags)
    return {
        "engagement_level": engagement_level,
        "preferred_arm": preferred_arm,
        "play_style": play_style,
        "risk_flags": risk_flags,
        "summary": summary,
    }


def _engagement_score(artifact: dict[str, Any]) -> int | float | None:
    summary = artifact.get("summary", {}) if isinstance(artifact.get("summary", {}), dict) else {}
    activity = summary.get("activity", {}) if isinstance(summary.get("activity", {}), dict) else {}
    score = activity.get("engagement_score")
    return score if isinstance(score, int | float) else None


def _outcome(artifact: dict[str, Any]) -> str | None:
    report = artifact.get("report", {}) if isinstance(artifact.get("report", {}), dict) else {}
    return report.get("outcome")


def _engagement_level(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


def _play_style(engagement_level: str, risk_flags: list[str]) -> str:
    if "error" in risk_flags or "lost_target" in risk_flags:
        return "谨慎探索型"
    if engagement_level == "high":
        return "主动追逐型"
    if engagement_level == "medium":
        return "稳定观察型"
    return "慢热试探型"


def _summary(preferred_arm: str, engagement_level: str, risk_flags: list[str]) -> str:
    if risk_flags:
        return f"这只猫对慢速、可预测的互动更稳定，当前推荐 {preferred_arm}。"
    if engagement_level == "high":
        return f"这只猫参与度高，可以用 {preferred_arm} 保持节奏明确的互动。"
    if engagement_level == "medium":
        return f"这只猫参与度中等，适合用 {preferred_arm} 做稳定试探。"
    return f"这只猫还在慢热观察，建议用 {preferred_arm} 低强度开始。"
