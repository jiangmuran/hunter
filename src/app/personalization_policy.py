from __future__ import annotations

from typing import Any

from src.app.session_memory import memory_preferences


DEFAULT_PLAY_ARM = "wand_slow"


def recommend_play_arm(memory_box: Any | None = None, default_arm: str = DEFAULT_PLAY_ARM) -> dict[str, Any]:
    if memory_box is None:
        return {"arm": default_arm, "source": "default", "expected_reward": None}

    preferences = memory_preferences(memory_box, limit=1)
    if not preferences:
        return {"arm": default_arm, "source": "default", "expected_reward": None}

    preference = preferences[0]
    return {
        "arm": preference["arm"],
        "source": "memory",
        "expected_reward": preference["expected_reward"],
    }


def build_personalization_preview(
    memory_box: Any | None = None,
    default_arm: str = DEFAULT_PLAY_ARM,
    limit: int = 3,
) -> dict[str, Any]:
    recommendation = recommend_play_arm(memory_box=memory_box, default_arm=default_arm)
    preferences = memory_preferences(memory_box, limit=limit) if memory_box is not None else []
    return {
        "recommended_arm": recommendation["arm"],
        "source": recommendation["source"],
        "expected_reward": recommendation["expected_reward"],
        "preferences": preferences,
    }
