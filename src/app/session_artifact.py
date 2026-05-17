from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def build_session_artifact(
    session: dict[str, Any],
    mode: str,
    scenario: str,
    session_id: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> dict[str, Any]:
    now = _utc_now()
    artifact = {
        "id": session_id or f"session-{uuid4().hex}",
        "mode": mode,
        "scenario": scenario,
        "started_at": started_at or now,
        "ended_at": ended_at or now,
        "states": session.get("states", []),
        "events": session.get("events", []),
        "summary": session.get("summary", {}),
        "report": session.get("report", {}),
    }
    if "memory_update" in session:
        artifact["memory_update"] = session["memory_update"]
    return artifact


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
