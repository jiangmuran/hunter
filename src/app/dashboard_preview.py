from __future__ import annotations

from collections import Counter
from typing import Any


TITLE = "Hunter 软件 MVP 仪表盘预览"


def build_dashboard_preview(
    artifacts: list[dict[str, Any]],
    memory_preferences: list[dict[str, Any]] | None = None,
    milestone: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recent = sorted(artifacts, key=lambda artifact: artifact.get("ended_at", ""), reverse=True)
    outcome_counts = Counter(
        artifact.get("report", {}).get("outcome")
        for artifact in artifacts
        if artifact.get("report", {}).get("outcome")
    )
    command_totals = Counter()
    for artifact in artifacts:
        command_totals.update(artifact.get("summary", {}).get("command_counts", {}))

    preview = {
        "title": TITLE,
        "total_sessions": len(artifacts),
        "outcome_counts": dict(outcome_counts),
        "command_totals": dict(command_totals),
        "latest_session": _session_card(recent[0]) if recent else None,
        "recent_sessions": [_session_card(artifact) for artifact in recent[:5]],
        "memory_preferences": memory_preferences or [],
    }
    if milestone is not None:
        preview["milestone"] = milestone
    return preview


def _session_card(artifact: dict[str, Any]) -> dict[str, Any]:
    report = artifact.get("report", {})
    return {
        "id": artifact.get("id"),
        "scenario": artifact.get("scenario"),
        "outcome": report.get("outcome"),
        "title": report.get("title"),
    }
