from __future__ import annotations

from typing import Any


def build_session_report(summary: dict[str, Any]) -> dict[str, Any]:
    outcome = _outcome(summary)
    title = _title(outcome)
    lines = [
        title,
        f"运行 tick：{summary.get('ticks', 0)}",
        f"最终状态：{summary.get('final_state')}",
        f"最后动作：{summary.get('last_action')}",
        f"状态轨迹：{_format_counts(summary.get('state_counts', {}))}",
        f"动作统计：{_format_counts(summary.get('command_counts', {}))}",
    ]
    if summary.get("error"):
        lines.append(f"错误：{summary['error']}")

    return {
        "outcome": outcome,
        "title": title,
        "command_line": _format_counts(summary.get("command_counts", {})),
        "lines": lines,
        "text": "\n".join(lines),
    }


def _outcome(summary: dict[str, Any]) -> str:
    if summary.get("error") or summary.get("final_state") == "error" or not summary.get("healthy", True):
        return "error"
    if summary.get("lost_target"):
        return "lost_target"
    if summary.get("reached_stop_distance"):
        return "success"
    if summary.get("target_seen"):
        return "partial"
    return "no_target"


def _title(outcome: str) -> str:
    titles = {
        "success": "看到了猫，并安全靠近到制动距离",
        "lost_target": "中途丢失目标，已安全停车",
        "error": "发生异常，已停车",
        "partial": "看到了猫，但还没有完成靠近",
        "no_target": "本次没有看到猫，保持安全待机",
    }
    return titles[outcome]


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "无"
    return "，".join(f"{name} × {count}" for name, count in counts.items())
