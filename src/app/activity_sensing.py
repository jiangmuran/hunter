from __future__ import annotations

from typing import Any


def build_activity_score(target: dict[str, Any] | None, sample: dict[str, Any] | None = None) -> dict[str, Any]:
    sample = sample or {}
    motion = float(sample.get("motion_score", 0.0) or 0.0)
    visible_ratio = float(sample.get("visible_ratio", 1.0 if target else 0.0) or 0.0)
    target_bonus = 0.2 if target else 0.0
    score = max(0.0, min(1.0, motion * 0.55 + visible_ratio * 0.25 + target_bonus))
    if score >= 0.7:
        level = "high"
    elif score >= 0.35:
        level = "medium"
    else:
        level = "low"
    return {
        "score": round(score, 3),
        "level": level,
        "window_seconds": int(sample.get("window_seconds", 10) or 10),
        "source": "hardware_sample" if sample else "target_visibility",
    }
