from __future__ import annotations

from typing import Any


def build_enhanced_report(
    session_report: dict[str, Any],
    strategy: dict[str, Any],
    profile: dict[str, Any],
    next_session_plan: dict[str, Any],
) -> dict[str, Any]:
    title = "Hunter 软件智能报告"

    sections = [
        {
            "heading": "基础互动结果",
            "content": session_report.get("text", ""),
        },
        {
            "heading": "Agent 策略判断",
            "content": _format_strategy(strategy),
        },
        {
            "heading": "猫咪画像",
            "content": _format_profile(profile),
        },
        {
            "heading": "下一轮互动计划",
            "content": _format_next_session_plan(next_session_plan),
        },
    ]

    text_lines = [title, ""]
    for section in sections:
        text_lines.append(f"## {section['heading']}")
        text_lines.append(section["content"])
        text_lines.append("")
    text = "\n".join(text_lines)

    return {
        "title": title,
        "sections": sections,
        "text": text,
    }


def _format_strategy(strategy: dict[str, Any]) -> str:
    decision = strategy.get("decision", "")
    reason = strategy.get("reason", "")
    next_action = strategy.get("next_action", "")
    lines = [
        f"决策：{decision}",
    ]
    if reason:
        lines.append(f"理由：{reason}")
    if next_action:
        lines.append(f"下一步动作：{next_action}")
    return "\n".join(lines)


def _format_profile(profile: dict[str, Any]) -> str:
    play_style = profile.get("play_style", "")
    summary = profile.get("summary", "")
    lines = []
    if play_style:
        lines.append(f"玩耍风格：{play_style}")
    if summary:
        lines.append(f"画像概要：{summary}")
    return "\n".join(lines)


def _format_next_session_plan(plan: dict[str, Any]) -> str:
    arm = plan.get("recommended_arm", "")
    intensity = plan.get("intensity", "")
    note = plan.get("operator_note", "")
    lines = []
    if arm:
        lines.append(f"推荐机械臂动作：{arm}")
    if intensity:
        lines.append(f"互动强度：{intensity}")
    if note:
        lines.append(f"操作员提示：{note}")
    return "\n".join(lines)
