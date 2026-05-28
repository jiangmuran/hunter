from __future__ import annotations

from typing import Any

DEFAULT_ARM = "wand_slow"


def build_cat_profile(
    artifacts: list[dict[str, Any]],
    memory_preferences: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if memory_preferences is None:
        memory_preferences = []

    # Determine preferred arm from memory or default
    if memory_preferences:
        preferred_arm = memory_preferences[0]["arm"]
    else:
        preferred_arm = DEFAULT_ARM

    # Calculate engagement level from artifact activity scores
    if not artifacts:
        engagement_level = "unknown"
    else:
        scores = [
            artifact.get("summary", {}).get("activity", {}).get("engagement_score", 0)
            for artifact in artifacts
        ]
        avg_score = sum(scores) / len(scores)
        if avg_score >= 80:
            engagement_level = "high"
        elif avg_score >= 50:
            engagement_level = "medium"
        else:
            engagement_level = "low"

    # Map engagement level to play style
    play_style_map = {
        "high": "主动追逐型",
        "medium": "谨慎探索型",
        "low": "慵懒潜伏型",
        "unknown": "未知",
    }
    play_style = play_style_map[engagement_level]

    # Collect risk flags from non-success outcomes
    risk_flags = []
    for artifact in artifacts:
        outcome = artifact.get("report", {}).get("outcome", "")
        if outcome and outcome != "success":
            risk_flags.append(outcome)

    # Build human-readable summary
    arm_speed_map: dict[str, str] = {
        "wand_slow": "慢速",
        "laser_escape": "快速",
        "wand_hover": "悬停",
        "wand_fast": "快速",
    }
    speed = arm_speed_map.get(preferred_arm, "默认")

    if not artifacts:
        summary = f"还没有足够的数据来生成猫咪画像。建议使用{speed}玩法开始互动。"
    else:
        summary = f"猫咪互动{len(artifacts)}次，活跃度为{engagement_level}，偏好{speed}玩法{preferred_arm}。"
        if risk_flags:
            summary += f" 注意风险：{'、'.join(risk_flags)}。"

    return {
        "engagement_level": engagement_level,
        "preferred_arm": preferred_arm,
        "play_style": play_style,
        "risk_flags": risk_flags,
        "summary": summary,
    }
