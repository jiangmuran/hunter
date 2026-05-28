"""Interaction strategy module for making session-level decisions.

Provides two entry points:
- build_interaction_strategy: decides next action from a single session summary.
- build_suite_strategy: decides recovery/continuation across a series of session artifacts.
"""

from __future__ import annotations

from typing import Any


def build_interaction_strategy(summary: dict[str, Any]) -> dict[str, Any]:
    """Return a strategic decision based on a single session summary.

    Decisions: continue_engagement, search_again, safe_pause, recovery_check.
    """
    # Error or unhealthy system -> safe_pause
    if summary.get("error") or not summary.get("healthy", True):
        return {
            "decision": "safe_pause",
            "confidence": "high",
            "reason": "系统出现异常，进入安全暂停状态",
            "next_action": "触发安全暂停，等待人工介入",
        }

    # Success: reached stop distance with good engagement -> continue
    if summary.get("reached_stop_distance"):
        activity = summary.get("activity", {})
        if activity.get("engagement_score", 0) >= 50:
            return {
                "decision": "continue_engagement",
                "confidence": "high",
                "reason": "已安全靠近目标，保持互动",
                "next_action": "维持当前互动距离，继续采集数据",
            }

    # Lost target -> safe_pause
    if summary.get("lost_target"):
        return {
            "decision": "safe_pause",
            "confidence": "medium",
            "reason": "目标丢失，需要重新定位",
            "next_action": "保守暂停，等待重新搜索指令",
        }

    # No target seen -> search_again
    if not summary.get("target_seen", True):
        return {
            "decision": "search_again",
            "confidence": "medium",
            "reason": "未发现目标",
            "next_action": "执行重新搜索，扩大扫描范围",
        }

    # Default fallback
    return {
        "decision": "search_again",
        "confidence": "low",
        "reason": "默认行为：未满足其他条件，重新搜索",
        "next_action": "重新搜索",
    }


def build_suite_strategy(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze a series of session artifacts and decide on a recovery strategy.

    Repeated poor outcomes (lost_target, error, no_target) trigger recovery_check.
    """
    # Count outcomes from reports
    bad_outcomes = 0
    for artifact in artifacts:
        report = artifact.get("report", {})
        outcome = report.get("outcome", "")
        if outcome in ("lost_target", "error", "no_target"):
            bad_outcomes += 1

    if bad_outcomes >= len(artifacts):
        return {
            "decision": "recovery_check",
            "confidence": "high",
            "reason": "连续多次不良结果，触发系统恢复检查",
            "next_action": "全面系统诊断，重置搜索策略",
        }

    if bad_outcomes >= 2:
        return {
            "decision": "recovery_check",
            "confidence": "medium",
            "reason": "多次不良结果，建议系统恢复检查",
            "next_action": "检查系统状态，调整搜索参数",
        }

    return {
        "decision": "continue_engagement",
        "confidence": "medium",
        "reason": "整体表现正常，继续当前策略",
        "next_action": "保持当前搜索和互动模式",
    }
