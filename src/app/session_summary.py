from __future__ import annotations

import math
from collections import Counter
from typing import Any

from src.app.events import AppEvent, EventKind


def _build_trajectory(states: list[dict[str, Any]]) -> dict[str, Any]:
    points: list[tuple[float, float]] = []
    for state in states:
        target = state.get("target")
        if target and "cx" in target and "cy" in target:
            points.append((target["cx"], target["cy"]))

    path_length = 0.0
    for i in range(1, len(points)):
        x1, y1 = points[i - 1]
        x2, y2 = points[i]
        path_length += math.hypot(x2 - x1, y2 - y1)

    return {
        "points": points,
        "point_count": len(points),
        "path_length": round(path_length, 2),
    }


def _build_activity(states: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(states)
    if total == 0:
        return {
            "target_visible_ticks": 0,
            "moving_ticks": 0,
            "engagement_score": 0,
        }

    target_visible_ticks = sum(1 for s in states if s.get("target") and "cx" in s["target"] and "cy" in s["target"])
    moving_actions = {"forward", "rotate_cw", "rotate_ccw"}
    moving_ticks = sum(1 for s in states if s.get("last_action") in moving_actions)

    engagement_score = round(target_visible_ticks / total * 100)

    return {
        "target_visible_ticks": target_visible_ticks,
        "moving_ticks": moving_ticks,
        "engagement_score": engagement_score,
    }


def summarize_session(states: list[dict[str, Any]], events: list[AppEvent | dict[str, Any]]) -> dict[str, Any]:
    final_state = states[-1] if states else {}
    state_counts = Counter(state.get("state") for state in states if state.get("state"))
    command_counts = Counter(_event_message(event) for event in events if _event_kind(event) == EventKind.COMMAND.value)
    target_seen = any(bool(state.get("target")) for state in states)
    lost_target = "lost_target" in state_counts
    reached_stop_distance = "at_stop_distance" in state_counts
    error = next((state.get("error") for state in reversed(states) if state.get("error")), None)
    healthy = bool(final_state.get("healthy", True)) if states else True

    highlights = []
    if target_seen:
        highlights.append("target acquired during session")
    if reached_stop_distance:
        highlights.append("approached target and stopped at safe distance")
    if lost_target:
        highlights.append("target was lost after acquisition")
    if error:
        highlights.append("session ended in error")

    return {
        "ticks": len(states),
        "final_state": final_state.get("state"),
        "healthy": healthy,
        "error": error,
        "target_seen": target_seen,
        "lost_target": lost_target,
        "reached_stop_distance": reached_stop_distance,
        "last_action": final_state.get("last_action"),
        "state_counts": dict(state_counts),
        "command_counts": dict(command_counts),
        "highlights": highlights,
        "trajectory": _build_trajectory(states),
        "activity": _build_activity(states),
    }


def _event_kind(event: AppEvent | dict[str, Any]) -> str | None:
    if isinstance(event, AppEvent):
        return event.kind.value
    kind = event.get("kind")
    if isinstance(kind, EventKind):
        return kind.value
    return kind


def _event_message(event: AppEvent | dict[str, Any]) -> str | None:
    if isinstance(event, AppEvent):
        return event.message
    return event.get("message")
