from __future__ import annotations

from collections import Counter
from typing import Any

from src.app.session_highlights import build_session_highlights


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

    engagement_scores = [
        artifact.get("summary", {}).get("activity", {}).get("engagement_score", 0)
        for artifact in artifacts
    ]
    total_path_length = sum(
        artifact.get("summary", {}).get("trajectory", {}).get("path_length", 0)
        for artifact in artifacts
    )
    state_timeline = [
        state.get("state")
        for artifact in recent
        for state in artifact.get("states", [])
        if state.get("state")
    ]

    preview = {
        "title": TITLE,
        "total_sessions": len(artifacts),
        "outcome_counts": dict(outcome_counts),
        "command_totals": dict(command_totals),
        "latest_session": _session_card(recent[0]) if recent else None,
        "recent_sessions": [_session_card(artifact) for artifact in recent[:5]],
        "memory_preferences": memory_preferences or [],
        "activity": {
            "average_engagement_score": round(sum(engagement_scores) / len(engagement_scores)) if engagement_scores else 0,
        },
        "trajectory": {
            "total_path_length": round(total_path_length, 2),
        },
        "state_timeline": state_timeline,
        "highlights": build_session_highlights(recent),
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
