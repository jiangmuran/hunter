from __future__ import annotations

from typing import Any


def build_next_session_plan(
    profile: dict[str, Any],
    strategy: dict[str, Any],
    personalization: dict[str, Any],
) -> dict[str, Any]:
    decision = strategy.get("decision", "")

    if decision in ("safe_pause", "recovery_check"):
        return _plan(
            recommended_arm=profile.get("preferred_arm", "wand_slow"),
            scenario_focus="lost_target",
            intensity="low",
            operator_note=(
                f"上一次互动需要保守暂停，本次以慢速玩法 {profile.get('preferred_arm', 'wand_slow')} "
                f"进行恢复性观察，关注目标是否重新出现。"
            ),
        )

    if decision == "continue_engagement":
        return _plan(
            recommended_arm=personalization.get("recommended_arm", "wand_slow"),
            scenario_focus="approach",
            intensity="medium",
            operator_note=(
                f"上一次互动质量较好，本次使用 {personalization.get('recommended_arm', 'wand_slow')} "
                f"继续保持互动节奏。"
            ),
        )

    # search_again (default fallback)
    return _plan(
        recommended_arm=profile.get("preferred_arm", "wand_slow"),
        scenario_focus="empty",
        intensity="low",
        operator_note=(
            f"上一次没有发现稳定目标，本次以 {profile.get('preferred_arm', 'wand_slow')} "
            f"重新搜索目标区域。"
        ),
    )


def _plan(
    recommended_arm: str,
    scenario_focus: str,
    intensity: str,
    operator_note: str,
) -> dict[str, Any]:
    return {
        "recommended_arm": recommended_arm,
        "scenario_focus": scenario_focus,
        "intensity": intensity,
        "operator_note": operator_note,
    }
