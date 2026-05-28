from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable


def aggregate_daily_sessions(artifacts: list[dict[str, Any]], target_date: str | None = None) -> dict[str, Any]:
    date = target_date or datetime.now(timezone.utc).date().isoformat()
    daily_artifacts = [artifact for artifact in artifacts if _artifact_date(artifact) == date]

    outcome_counts = Counter(
        artifact.get("report", {}).get("outcome")
        for artifact in daily_artifacts
        if artifact.get("report", {}).get("outcome")
    )
    command_totals = Counter()
    highlights = []
    story_highlights = []
    for artifact in daily_artifacts:
        summary = artifact.get("summary", {})
        command_totals.update(summary.get("command_counts", {}))
        highlights.extend(summary.get("highlights", []))
        highlight = artifact.get("highlight")
        if isinstance(highlight, dict) and highlight.get("story"):
            story_highlights.append(highlight["story"])

    return {
        "date": date,
        "total_sessions": len(daily_artifacts),
        "outcome_counts": dict(outcome_counts),
        "command_totals": dict(command_totals),
        "highlights": highlights,
        "story_highlights": story_highlights,
    }


def build_daily_diary_prompt(stats: dict[str, Any]) -> str:
    return "\n".join([
        "请用猫咪第一人称写一段简短中文日报，只能基于以下事实，不要编造新事件。",
        f"日期：{stats.get('date')}",
        f"互动次数：{stats.get('total_sessions', 0)}",
        f"结果统计：{_format_counts(stats.get('outcome_counts', {}))}",
        f"动作统计：{_format_counts(stats.get('command_totals', {}))}",
        f"关键记录：{_format_highlights(stats.get('highlights', []))}",
        f"故事素材：{_format_highlights(stats.get('story_highlights', []))}",
    ])


def build_daily_diary(stats: dict[str, Any], llm_fn: Callable[[str], str] | None = None) -> dict[str, Any]:
    prompt = build_daily_diary_prompt(stats)
    if llm_fn is not None:
        return {"mode": "llm", "stats": stats, "prompt": prompt, "text": llm_fn(prompt)}
    return {"mode": "template", "stats": stats, "prompt": prompt, "text": _template_text(stats)}


def build_daily_diary_from_sessions(
    artifacts: list[dict[str, Any]],
    target_date: str | None = None,
    llm_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    stats = aggregate_daily_sessions(artifacts, target_date=target_date)
    return build_daily_diary(stats, llm_fn=llm_fn)


def _artifact_date(artifact: dict[str, Any]) -> str | None:
    ended_at = artifact.get("ended_at", "")
    if "T" in ended_at:
        return ended_at.split("T", 1)[0]
    return ended_at or None


def _template_text(stats: dict[str, Any]) -> str:
    date = stats.get("date")
    total_sessions = stats.get("total_sessions", 0)
    if total_sessions == 0:
        return f"{date}：今天还没有互动记录，我先安静观察环境。"

    lines = [
        f"{date}：今天我和 Hunter 互动了 {total_sessions} 次。",
        f"结果统计：{_format_counts(stats.get('outcome_counts', {}))}。",
        f"动作统计：{_format_counts(stats.get('command_totals', {}))}。",
    ]
    highlights = stats.get("highlights", [])
    if highlights:
        lines.append(f"印象最深的是：{_format_highlights(highlights)}。")
    story_highlights = stats.get("story_highlights", [])
    if story_highlights:
        lines.append(f"可以写成故事的是：{_format_highlights(story_highlights)}。")
    return "\n".join(lines)


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "无"
    return "，".join(f"{key}: {value}" for key, value in counts.items())


def _format_highlights(highlights: list[str]) -> str:
    if not highlights:
        return "无"
    return "；".join(highlights)
