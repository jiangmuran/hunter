from __future__ import annotations

from typing import Any


DEFAULT_MEMORY_ARM_MAP = {
    "approach": "laser_escape",
    "track_target": "wand_hover",
    "safe_stop": "wand_slow",
}


def session_memory_update(summary: dict[str, Any]) -> dict[str, Any] | None:
    # Keep this as a pure mapping so mock MVP can prove memory semantics without touching SQLite.
    if summary.get("error"):
        return {"arm": "safe_stop", "reward": 0, "reason": "error"}
    if summary.get("reached_stop_distance"):
        return {"arm": "approach", "reward": 1, "reason": "reached_stop_distance"}
    if summary.get("lost_target"):
        return {"arm": "track_target", "reward": 0, "reason": "lost_target"}
    if summary.get("target_seen"):
        return {"arm": "track_target", "reward": 1, "reason": "target_seen"}
    return None


def apply_session_memory_update(
    summary: dict[str, Any],
    memory_box: Any,
    arm_map: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    update = session_memory_update(summary)
    if update is None:
        return None

    mapping = arm_map or DEFAULT_MEMORY_ARM_MAP
    memory_arm = mapping[update["arm"]]
    memory_box.update(memory_arm, update["reward"])
    return {
        "app_arm": update["arm"],
        "memory_arm": memory_arm,
        "reward": update["reward"],
        "reason": update["reason"],
    }


def memory_preferences(memory_box: Any, limit: int = 3) -> list[dict[str, Any]]:
    return [
        {"arm": arm, "expected_reward": expected_reward}
        for arm, expected_reward in memory_box.top_preferences(limit)
    ]
