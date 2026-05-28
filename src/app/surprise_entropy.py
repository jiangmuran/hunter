from __future__ import annotations

from typing import Any


CANDIDATES = [
    {
        "name": "wand_slow_sweep",
        "label": "慢速逗猫棒扫动",
        "intensity": "low",
        "arm": "wand_slow",
        "base_engagement": 0.65,
    },
    {
        "name": "wand_hover_tease",
        "label": "逗猫棒悬停挑逗",
        "intensity": "medium",
        "arm": "wand_hover",
        "base_engagement": 0.75,
    },
    {
        "name": "laser_escape_short",
        "label": "短距离激光逃逸",
        "intensity": "high",
        "arm": "laser_escape",
        "base_engagement": 0.9,
    },
    {
        "name": "pause_observe",
        "label": "暂停观察",
        "intensity": "low",
        "arm": "none",
        "base_engagement": 0.35,
    },
    {
        "name": "reward_cue",
        "label": "奖励提示",
        "intensity": "low",
        "arm": "reward_cue",
        "base_engagement": 0.55,
    },
]

RISKY_INTENSITIES = {"high"}
SAFE_DECISIONS = {"safe_pause", "recovery_check"}
RISK_FLAGS = {"lost_target", "error"}


def build_surprise_entropy_preview(
    profile: dict[str, Any],
    strategy: dict[str, Any],
    personalization: dict[str, Any],
    recent_outcomes: list[str] | None = None,
    recent_actions: list[str] | None = None,
) -> dict[str, Any]:
    outcomes = list(recent_outcomes or [])
    actions = list(recent_actions or [])
    safety_gate = _build_safety_gate(profile, strategy, outcomes)
    preferred_arm = _preferred_arm(profile, personalization)
    engagement_level = profile.get("engagement_level", "unknown")

    candidates = []
    for index, candidate in enumerate(CANDIDATES):
        scored = _score_candidate(
            candidate,
            index,
            preferred_arm,
            engagement_level,
            profile,
            strategy,
            actions,
            safety_gate,
        )
        candidates.append(scored)

    selectable = [candidate for candidate in candidates if candidate["safety_allowed"]]
    if not selectable:
        selectable = [candidate for candidate in candidates if candidate["name"] == "pause_observe"]
    selected = sorted(selectable, key=lambda item: (-item["entropy_score"], item["rank"]))[0]
    selected_action = {key: value for key, value in selected.items() if key != "rank"}

    return {
        "engine": "surprise_entropy",
        "selected_action": selected_action,
        "candidates": [{key: value for key, value in candidate.items() if key != "rank"} for candidate in candidates],
        "safety_gate": safety_gate,
        "recent_outcomes": outcomes,
        "recent_actions": actions,
    }


def _build_safety_gate(profile: dict[str, Any], strategy: dict[str, Any], recent_outcomes: list[str]) -> dict[str, Any]:
    decision = strategy.get("decision", "")
    risk_flags = set(profile.get("risk_flags", [])) if isinstance(profile.get("risk_flags", []), list) else set()
    risky_outcomes = [outcome for outcome in recent_outcomes[-3:] if outcome in RISK_FLAGS]
    if decision in SAFE_DECISIONS:
        return {
            "allowed": False,
            "reason": "当前策略要求安全暂停，优先观察而不是制造新刺激。",
        }
    if risk_flags & RISK_FLAGS or risky_outcomes:
        return {
            "allowed": True,
            "reason": "存在丢失目标或异常风险，只允许低风险惊喜动作。",
        }
    return {
        "allowed": True,
        "reason": "当前状态稳定，可以选择带有新鲜感的互动动作。",
    }


def _preferred_arm(profile: dict[str, Any], personalization: dict[str, Any]) -> str:
    recommended = personalization.get("recommended_arm")
    if isinstance(recommended, str) and recommended:
        return recommended
    preferred = profile.get("preferred_arm")
    if isinstance(preferred, str) and preferred:
        return preferred
    return "wand_slow"


def _score_candidate(
    candidate: dict[str, Any],
    rank: int,
    preferred_arm: str,
    engagement_level: str,
    profile: dict[str, Any],
    strategy: dict[str, Any],
    recent_actions: list[str],
    safety_gate: dict[str, Any],
) -> dict[str, Any]:
    novelty = _novelty(candidate["name"], recent_actions)
    preference_match = 1.0 if candidate["arm"] == preferred_arm else 0.45
    if candidate["name"] == "pause_observe" and candidate["arm"] != preferred_arm:
        preference_match = 0.55
    engagement_fit = _engagement_fit(candidate["intensity"], engagement_level, strategy.get("decision", ""))
    risk_penalty = _risk_penalty(candidate["intensity"], profile, strategy)
    safety_allowed = _safety_allowed(candidate, safety_gate, profile, strategy)
    entropy_score = round(
        (novelty * 0.35 + preference_match * 0.25 + engagement_fit * 0.3 + candidate["base_engagement"] * 0.1 - risk_penalty),
        3,
    )
    if not safety_allowed:
        entropy_score = round(entropy_score - 1.0, 3)

    return {
        "rank": rank,
        "name": candidate["name"],
        "label": candidate["label"],
        "intensity": candidate["intensity"],
        "arm": candidate["arm"],
        "novelty": novelty,
        "preference_match": preference_match,
        "engagement_fit": engagement_fit,
        "risk_penalty": risk_penalty,
        "entropy_score": entropy_score,
        "safety_allowed": safety_allowed,
        "reason": _candidate_reason(candidate, novelty, preference_match, risk_penalty, safety_allowed),
    }


def _novelty(action_name: str, recent_actions: list[str]) -> float:
    repeats = recent_actions[-5:].count(action_name)
    if repeats >= 3:
        return 0.1
    if repeats == 2:
        return 0.35
    if repeats == 1:
        return 0.65
    return 1.0


def _engagement_fit(intensity: str, engagement_level: str, decision: str) -> float:
    if decision in SAFE_DECISIONS:
        return 1.0 if intensity == "low" else 0.15
    if engagement_level == "high":
        return {"high": 1.0, "medium": 0.85, "low": 0.45}.get(intensity, 0.4)
    if engagement_level == "medium":
        return {"medium": 1.0, "low": 0.75, "high": 0.45}.get(intensity, 0.5)
    return {"low": 1.0, "medium": 0.55, "high": 0.2}.get(intensity, 0.5)


def _risk_penalty(intensity: str, profile: dict[str, Any], strategy: dict[str, Any]) -> float:
    risk_flags = set(profile.get("risk_flags", [])) if isinstance(profile.get("risk_flags", []), list) else set()
    if strategy.get("decision") in SAFE_DECISIONS:
        return 0.0 if intensity == "low" else 0.7
    if risk_flags & RISK_FLAGS:
        return {"high": 0.65, "medium": 0.25, "low": 0.0}.get(intensity, 0.2)
    return 0.0


def _safety_allowed(
    candidate: dict[str, Any],
    safety_gate: dict[str, Any],
    profile: dict[str, Any],
    strategy: dict[str, Any],
) -> bool:
    if strategy.get("decision") in SAFE_DECISIONS:
        return candidate["name"] == "pause_observe"
    risk_flags = set(profile.get("risk_flags", [])) if isinstance(profile.get("risk_flags", []), list) else set()
    if risk_flags & RISK_FLAGS and candidate["intensity"] in RISKY_INTENSITIES:
        return False
    return bool(safety_gate.get("allowed", True))


def _candidate_reason(
    candidate: dict[str, Any],
    novelty: float,
    preference_match: float,
    risk_penalty: float,
    safety_allowed: bool,
) -> str:
    if not safety_allowed:
        return "风险门控拦截该动作，当前不建议执行。"
    if candidate["name"] == "pause_observe":
        return "先暂停观察，降低对猫的压力并等待稳定信号。"
    if risk_penalty > 0:
        return "动作有一定风险扣分，仅在状态稳定时使用。"
    if preference_match >= 1.0 and novelty >= 0.65:
        return "动作符合当前偏好，并且近期重复度低。"
    if novelty < 0.5:
        return "近期已经重复过该动作，新鲜感较低。"
    return "动作在参与度和新鲜感之间保持平衡。"
