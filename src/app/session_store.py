from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


class SessionStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def save(self, artifact: dict[str, Any]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        artifacts = [existing for existing in self._load_all() if existing.get("id") != artifact.get("id")]
        artifacts.append(artifact)
        with self.path.open("w", encoding="utf-8") as file:
            for saved_artifact in artifacts:
                file.write(json.dumps(saved_artifact, ensure_ascii=False, sort_keys=True) + "\n")
        return artifact

    def get(self, session_id: str) -> dict[str, Any] | None:
        for artifact in reversed(self._load_all()):
            if artifact.get("id") == session_id:
                return artifact
        return None

    def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        artifacts = sorted(self._load_all(), key=lambda artifact: artifact.get("ended_at", ""), reverse=True)
        return artifacts[:limit]

    def overview(self) -> dict[str, Any]:
        artifacts = self._load_all()
        outcome_counts = Counter(
            artifact.get("report", {}).get("outcome")
            for artifact in artifacts
            if artifact.get("report", {}).get("outcome")
        )
        command_totals = Counter()
        for artifact in artifacts:
            command_totals.update(artifact.get("summary", {}).get("command_counts", {}))
        return {
            "total_sessions": len(artifacts),
            "outcome_counts": dict(outcome_counts),
            "command_totals": dict(command_totals),
        }

    def _load_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        artifacts = []
        with self.path.open("r", encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    artifacts.append(json.loads(line))
        return artifacts
