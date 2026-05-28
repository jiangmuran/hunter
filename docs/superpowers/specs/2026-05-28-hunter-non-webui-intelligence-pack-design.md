# Hunter Non-WebUI Intelligence Pack Design

**Goal:** Add a thin but broad set of non-WebUI software intelligence features that make Hunter look like a complete AI agent system during the Hackathon.

**Scope:** Python app-layer only. No Web UI work, no hardware integration work, and no motion-control refactor.

## User-facing outcome

A CLI/demo path should be able to show that Hunter can:

1. interpret a session outcome,
2. choose a next interaction strategy,
3. summarize the cat's profile from recent history,
4. recommend the next play plan,
5. produce a richer product-style report,
6. print a software intelligence brief for judges or teammates.

## Components

### `src/app/interaction_strategy.py`

Pure functions that convert a session summary into a strategy decision.

Decision IDs:

- `continue_engagement`: target reached stop distance or high engagement.
- `search_again`: no target or weak engagement without error.
- `safe_pause`: lost target, error, or unhealthy session.
- `recovery_check`: repeated poor outcomes in a suite/history.

Output shape:

```python
{
    "decision": "continue_engagement",
    "confidence": "high",
    "reason": "已经安全靠近目标，互动质量较高。",
    "next_action": "保持低强度互动，并观察猫是否继续感兴趣。",
}
```

### `src/app/cat_profile.py`

Pure functions that summarize recent artifacts and memory preferences into a demo-ready cat profile.

Output shape:

```python
{
    "engagement_level": "medium",
    "preferred_arm": "wand_slow",
    "play_style": "谨慎探索型",
    "risk_flags": ["lost_target"],
    "summary": "这只猫对慢速、可预测的互动更稳定。",
}
```

### `src/app/next_session_plan.py`

Combines profile, strategy, and personalization preview into the next interaction plan.

Output shape:

```python
{
    "recommended_arm": "wand_slow",
    "scenario_focus": "approach",
    "intensity": "low",
    "operator_note": "下一轮先慢速吸引，目标稳定后再靠近。",
}
```

### `src/app/enhanced_report.py`

Builds a richer Chinese software report from existing session report plus strategy/profile/plan.

Output shape:

```python
{
    "title": "Hunter 软件智能报告",
    "sections": [...],
    "text": "...",
}
```

### CLI integration in `src/app/demo.py`

Add `--software-intelligence-brief`.

It should run the existing product demo suite, build the profile/strategy/plan/enhanced report, and print a compact dict containing:

- `capabilities`
- `profile`
- `strategy`
- `next_session_plan`
- `enhanced_report`

This gives the Hackathon team a single non-WebUI command to show the intelligence layer.

## Data flow

```text
mock sessions
  -> session summary/report/artifacts
  -> memory updates + personalization preview
  -> cat profile
  -> latest-session strategy
  -> next-session plan
  -> enhanced report
  -> software intelligence brief CLI
```

## Testing

Add focused unit tests for each new module:

- strategy maps success/lost/error/no-target to correct decisions,
- profile summarizes engagement, preferred arm, risk flags,
- next-session plan chooses intensity and recommendation from inputs,
- enhanced report includes strategy/profile/plan content,
- CLI flag returns the software intelligence brief and does not require WebUI.

## Non-goals

- No Web UI changes.
- No browser/server work.
- No hardware endpoint checks.
- No long-running service.
- No new external dependency.
