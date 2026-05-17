from __future__ import annotations

from typing import Any

REQUIRED_OUTCOMES = {"no_target", "success", "lost_target", "error"}


def build_mvp_milestone(suite: dict[str, Any]) -> dict[str, Any]:
    outcome_counts = suite.get("outcome_counts", {})
    # These four outcomes are the local acceptance gate before real hardware bring-up.
    missing = sorted(REQUIRED_OUTCOMES - set(outcome_counts))
    complete = not missing

    return {
        "name": "no_hardware_mvp",
        "complete": complete,
        "headline": _headline(complete),
        "outcome_counts": outcome_counts,
        "missing_outcomes": missing,
        "completed_capabilities": [
            "mock scenario suite",
            "state/action/event trace",
            "session summary",
            "Chinese session report",
            "session memory update",
            "safe-stop validation",
        ],
        "next_phase": [
            "real robot closed loop",
            "real camera + YOLO detector",
            "MemoryBox persistence",
            "DailyDiary integration",
            "dashboard preview",
        ],
    }


def _headline(complete: bool) -> str:
    if complete:
        return "无硬件 MVP 已覆盖成功、空场、丢猫和异常四类核心路径。"
    return "无硬件 MVP 仍有核心路径未覆盖。"
