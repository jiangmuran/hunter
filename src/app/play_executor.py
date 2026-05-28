from __future__ import annotations

from typing import Any


ACTION_DEFAULTS = {
    "wand_fast": {"intensity": "medium", "duration_ms": 1200},
    "wand_hover": {"intensity": "low", "duration_ms": 1400},
    "laser_escape": {"intensity": "medium", "duration_ms": 1000},
    "laser_zigzag": {"intensity": "medium", "duration_ms": 1100},
    "sound_tease": {"intensity": "low", "duration_ms": 800},
}


def build_play_command(action: str, activity_level: str = "medium") -> dict[str, Any]:
    base = dict(ACTION_DEFAULTS.get(action, {"intensity": "low", "duration_ms": 800}))
    if activity_level == "low":
        base["intensity"] = "low"
    if activity_level == "high" and base["duration_ms"] > 1200:
        base["duration_ms"] = 1200
    return {"action": action, **base, "safety": "bounded"}


class PlayExecutor:
    def __init__(self, api: Any):
        self.api = api

    def execute(self, action: str, activity_level: str = "medium") -> dict[str, Any]:
        command = build_play_command(action, activity_level=activity_level)
        return self.api.execute_play_action(
            command["action"],
            intensity=command["intensity"],
            duration_ms=command["duration_ms"],
        )
