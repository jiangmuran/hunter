from __future__ import annotations

from typing import Any


def build_session_highlights(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one highlight card per artifact."""
    return [_build_card(artifact) for artifact in artifacts]


def _build_card(artifact: dict[str, Any]) -> dict[str, Any]:
    summary = artifact.get("summary", {})
    report = artifact.get("report", {})
    outcome = report.get("outcome", "unknown")

    return {
        "scenario": artifact.get("scenario", ""),
        "outcome": outcome,
        "tone": _tone(outcome),
        "title": report.get("title", ""),
        "story": _story(outcome),
        "detail": _detail(summary),
    }


def _tone(outcome: str) -> str:
    tones = {
        "success": "success",
        "error": "danger",
        "lost_target": "warning",
    }
    return tones.get(outcome, "calm")


def _story(outcome: str) -> str:
    stories = {
        "success": "Hunter 成功完成跟踪，安全靠近目标并保持在制动距离内",
        "lost_target": "目标在跟踪过程中消失，Hunter 已保守停车，等待重新检测",
        "error": "运行期间发生异常，Hunter 已安全停车",
        "no_target": "本次会话未检测到目标，Hunter 保持待机观察",
    }
    return stories.get(outcome, "Hunter 完成了一次交互记录")


def _detail(summary: dict[str, Any]) -> str:
    ticks = summary.get("ticks", 0)
    trajectory = summary.get("trajectory", {})
    point_count = trajectory.get("point_count", 0)
    path_length = trajectory.get("path_length", 0)
    activity = summary.get("activity", {})
    engagement_score = activity.get("engagement_score", 0)

    return (
        f"ticks={ticks}, "
        f"trajectory_points={point_count}, "
        f"path_length={path_length}, "
        f"engagement_score={engagement_score}"
    )
