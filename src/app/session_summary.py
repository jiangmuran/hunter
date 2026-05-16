from __future__ import annotations

from collections import Counter
from typing import Any

from src.app.events import AppEvent, EventKind


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
