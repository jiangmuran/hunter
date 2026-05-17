from __future__ import annotations

from typing import Any


ACTIVITY_SCORES = {
    "success": 0.9,
    "partial": 0.6,
    "lost_target": 0.5,
    "error": 0.1,
    "no_target": 0.1,
}


def log_session_to_report(
    summary: dict[str, Any],
    report: dict[str, Any],
    logger: Any,
    arm: str = "wand_slow",
) -> dict[str, Any]:
    outcome = report.get("outcome", "no_target")
    activity_score = ACTIVITY_SCORES.get(outcome, 0.1)
    logger.log_activity(activity_score)

    play_logged = False
    reward = 0
    duration = round(float(summary.get("ticks", 0)) * 0.1, 1)
    if summary.get("target_seen"):
        reward = 1 if outcome in {"success", "partial"} else 0
        logger.log_play(arm, reward, duration)
        play_logged = True

    return {
        "outcome": outcome,
        "activity_score": activity_score,
        "play_logged": play_logged,
        "arm": arm if play_logged else None,
        "reward": reward if play_logged else None,
        "duration": duration if play_logged else None,
    }
