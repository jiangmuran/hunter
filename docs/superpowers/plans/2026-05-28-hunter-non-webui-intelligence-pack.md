# Hunter Non-WebUI Intelligence Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add non-WebUI software intelligence features: interaction strategy, cat profile, next-session plan, enhanced report, and a CLI software intelligence brief.

**Architecture:** Implement small pure Python app-layer modules that consume existing session summaries, artifacts, memory preferences, and personalization previews. Wire them into `src/app/demo.py` through a new CLI flag without touching Web UI, hardware, or motion-control code.

**Tech Stack:** Python standard library, existing `src.app` modules, unittest/pytest test suite.

---

## File Structure

- Create `src/app/interaction_strategy.py` — maps a session summary/history into strategy decisions.
- Create `tests/test_app_interaction_strategy.py` — validates strategy mapping for success/no-target/lost/error/history recovery.
- Create `src/app/cat_profile.py` — builds a cat profile from artifacts and memory preferences.
- Create `tests/test_app_cat_profile.py` — validates engagement level, preferred arm, play style, risk flags.
- Create `src/app/next_session_plan.py` — combines profile, strategy, and personalization into next interaction plan.
- Create `tests/test_app_next_session_plan.py` — validates arm choice, intensity, scenario focus, operator note.
- Create `src/app/enhanced_report.py` — builds Chinese product-style report text from existing outputs.
- Create `tests/test_app_enhanced_report.py` — validates report sections and text content.
- Modify `src/app/demo.py` — add `--software-intelligence-brief`, build brief from existing product suite, and route CLI.
- Modify `tests/test_app_demo.py` — add CLI brief regression test.

### Task 1: Interaction Strategy

**Files:**
- Create: `src/app/interaction_strategy.py`
- Test: `tests/test_app_interaction_strategy.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_app_interaction_strategy.py`:

```python
import unittest


class InteractionStrategyTest(unittest.TestCase):
    def test_success_session_continues_engagement(self):
        from src.app.interaction_strategy import build_interaction_strategy

        strategy = build_interaction_strategy({
            "reached_stop_distance": True,
            "activity": {"engagement_score": 75},
            "healthy": True,
        })

        self.assertEqual(strategy["decision"], "continue_engagement")
        self.assertEqual(strategy["confidence"], "high")
        self.assertIn("安全靠近", strategy["reason"])

    def test_no_target_searches_again(self):
        from src.app.interaction_strategy import build_interaction_strategy

        strategy = build_interaction_strategy({"target_seen": False, "healthy": True})

        self.assertEqual(strategy["decision"], "search_again")
        self.assertEqual(strategy["confidence"], "medium")
        self.assertIn("重新搜索", strategy["next_action"])

    def test_lost_target_uses_safe_pause(self):
        from src.app.interaction_strategy import build_interaction_strategy

        strategy = build_interaction_strategy({"lost_target": True, "healthy": True})

        self.assertEqual(strategy["decision"], "safe_pause")
        self.assertIn("保守暂停", strategy["next_action"])

    def test_error_uses_safe_pause(self):
        from src.app.interaction_strategy import build_interaction_strategy

        strategy = build_interaction_strategy({"error": "detector failed", "healthy": False})

        self.assertEqual(strategy["decision"], "safe_pause")
        self.assertEqual(strategy["confidence"], "high")
        self.assertIn("异常", strategy["reason"])

    def test_history_with_repeated_poor_outcomes_requests_recovery_check(self):
        from src.app.interaction_strategy import build_suite_strategy

        strategy = build_suite_strategy([
            {"report": {"outcome": "lost_target"}},
            {"report": {"outcome": "error"}},
            {"report": {"outcome": "no_target"}},
        ])

        self.assertEqual(strategy["decision"], "recovery_check")
        self.assertEqual(strategy["confidence"], "high")
        self.assertIn("连续", strategy["reason"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_app_interaction_strategy.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.app.interaction_strategy'`.

- [ ] **Step 3: Implement strategy module**

Create `src/app/interaction_strategy.py`:

```python
from __future__ import annotations

from typing import Any


POOR_OUTCOMES = {"lost_target", "error", "no_target"}


def build_interaction_strategy(summary: dict[str, Any]) -> dict[str, Any]:
    activity = summary.get("activity", {}) if isinstance(summary.get("activity", {}), dict) else {}
    engagement_score = activity.get("engagement_score", 0)
    if summary.get("error") or summary.get("final_state") == "error" or not summary.get("healthy", True):
        return _strategy(
            "safe_pause",
            "high",
            "本次互动出现异常，Hunter 应先保证安全。",
            "保守暂停，检查感知和动作链路后再继续。",
        )
    if summary.get("lost_target"):
        return _strategy(
            "safe_pause",
            "high",
            "目标曾经出现但中途丢失，继续追逐会增加风险。",
            "保守暂停，等待目标重新稳定出现。",
        )
    if summary.get("reached_stop_distance") or engagement_score >= 70:
        return _strategy(
            "continue_engagement",
            "high",
            "已经安全靠近目标，互动质量较高。",
            "保持低强度互动，并观察猫是否继续感兴趣。",
        )
    if summary.get("target_seen"):
        return _strategy(
            "continue_engagement",
            "medium",
            "已经看到目标，但互动还没有形成稳定闭环。",
            "降低速度继续观察，确认目标稳定后再靠近。",
        )
    return _strategy(
        "search_again",
        "medium",
        "本次没有看到稳定目标，当前更适合继续搜索。",
        "重新搜索目标，并保持待机安全距离。",
    )


def build_suite_strategy(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = []
    for artifact in artifacts:
        report = artifact.get("report", {}) if isinstance(artifact.get("report", {}), dict) else {}
        outcome = report.get("outcome")
        if outcome:
            outcomes.append(outcome)
    if len(outcomes) >= 3 and all(outcome in POOR_OUTCOMES for outcome in outcomes[-3:]):
        return _strategy(
            "recovery_check",
            "high",
            "最近连续出现低质量结果，需要先恢复稳定性。",
            "先运行保守场景，确认感知稳定后再提高互动强度。",
        )
    latest_summary = artifacts[-1].get("summary", {}) if artifacts else {}
    if not isinstance(latest_summary, dict):
        latest_summary = {}
    return build_interaction_strategy(latest_summary)


def _strategy(decision: str, confidence: str, reason: str, next_action: str) -> dict[str, Any]:
    return {
        "decision": decision,
        "confidence": confidence,
        "reason": reason,
        "next_action": next_action,
    }
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_app_interaction_strategy.py -v`

Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src/app/interaction_strategy.py tests/test_app_interaction_strategy.py
git commit -m "feat(app): add interaction strategy decisions"
```

### Task 2: Cat Profile

**Files:**
- Create: `src/app/cat_profile.py`
- Test: `tests/test_app_cat_profile.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_app_cat_profile.py`:

```python
import unittest


class CatProfileTest(unittest.TestCase):
    def test_profile_summarizes_engagement_preference_and_risk(self):
        from src.app.cat_profile import build_cat_profile

        profile = build_cat_profile(
            [
                {"summary": {"activity": {"engagement_score": 80}}, "report": {"outcome": "success"}},
                {"summary": {"activity": {"engagement_score": 40}}, "report": {"outcome": "lost_target"}},
            ],
            [{"arm": "wand_slow", "expected_reward": 0.8}],
        )

        self.assertEqual(profile["engagement_level"], "medium")
        self.assertEqual(profile["preferred_arm"], "wand_slow")
        self.assertEqual(profile["play_style"], "谨慎探索型")
        self.assertIn("lost_target", profile["risk_flags"])
        self.assertIn("慢速", profile["summary"])

    def test_profile_uses_default_when_history_is_empty(self):
        from src.app.cat_profile import build_cat_profile

        profile = build_cat_profile([], [])

        self.assertEqual(profile["engagement_level"], "unknown")
        self.assertEqual(profile["preferred_arm"], "wand_slow")
        self.assertEqual(profile["risk_flags"], [])
        self.assertIn("还没有", profile["summary"])

    def test_profile_marks_high_engagement_as_active(self):
        from src.app.cat_profile import build_cat_profile

        profile = build_cat_profile(
            [{"summary": {"activity": {"engagement_score": 90}}, "report": {"outcome": "success"}}],
            [{"arm": "laser_escape", "expected_reward": 0.9}],
        )

        self.assertEqual(profile["engagement_level"], "high")
        self.assertEqual(profile["play_style"], "主动追逐型")
        self.assertEqual(profile["preferred_arm"], "laser_escape")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_app_cat_profile.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.app.cat_profile'`.

- [ ] **Step 3: Implement profile module**

Create `src/app/cat_profile.py`:

```python
from __future__ import annotations

from typing import Any


DEFAULT_ARM = "wand_slow"


def build_cat_profile(
    artifacts: list[dict[str, Any]],
    memory_preferences: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    preferences = memory_preferences or []
    preferred_arm = preferences[0]["arm"] if preferences else DEFAULT_ARM
    scores = [_engagement_score(artifact) for artifact in artifacts]
    scores = [score for score in scores if score is not None]
    outcomes = [_outcome(artifact) for artifact in artifacts]
    outcomes = [outcome for outcome in outcomes if outcome]
    risk_flags = sorted({outcome for outcome in outcomes if outcome in {"lost_target", "error"}})

    if not artifacts:
        return {
            "engagement_level": "unknown",
            "preferred_arm": preferred_arm,
            "play_style": "待观察型",
            "risk_flags": [],
            "summary": "还没有足够历史记录，先使用慢速默认互动观察猫咪反应。",
        }

    average_score = round(sum(scores) / len(scores)) if scores else 0
    engagement_level = _engagement_level(average_score)
    play_style = _play_style(engagement_level, risk_flags)
    summary = _summary(preferred_arm, engagement_level, risk_flags)
    return {
        "engagement_level": engagement_level,
        "preferred_arm": preferred_arm,
        "play_style": play_style,
        "risk_flags": risk_flags,
        "summary": summary,
    }


def _engagement_score(artifact: dict[str, Any]) -> int | None:
    summary = artifact.get("summary", {}) if isinstance(artifact.get("summary", {}), dict) else {}
    activity = summary.get("activity", {}) if isinstance(summary.get("activity", {}), dict) else {}
    score = activity.get("engagement_score")
    return score if isinstance(score, int | float) else None


def _outcome(artifact: dict[str, Any]) -> str | None:
    report = artifact.get("report", {}) if isinstance(artifact.get("report", {}), dict) else {}
    return report.get("outcome")


def _engagement_level(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


def _play_style(engagement_level: str, risk_flags: list[str]) -> str:
    if "error" in risk_flags or "lost_target" in risk_flags:
        return "谨慎探索型"
    if engagement_level == "high":
        return "主动追逐型"
    if engagement_level == "medium":
        return "稳定观察型"
    return "慢热试探型"


def _summary(preferred_arm: str, engagement_level: str, risk_flags: list[str]) -> str:
    if risk_flags:
        return f"这只猫对慢速、可预测的互动更稳定，当前推荐 {preferred_arm}。"
    if engagement_level == "high":
        return f"这只猫参与度高，可以用 {preferred_arm} 保持节奏明确的互动。"
    if engagement_level == "medium":
        return f"这只猫参与度中等，适合用 {preferred_arm} 做稳定试探。"
    return f"这只猫还在慢热观察，建议用 {preferred_arm} 低强度开始。"
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_app_cat_profile.py -v`

Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/app/cat_profile.py tests/test_app_cat_profile.py
git commit -m "feat(app): summarize cat interaction profile"
```

### Task 3: Next Session Plan

**Files:**
- Create: `src/app/next_session_plan.py`
- Test: `tests/test_app_next_session_plan.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_app_next_session_plan.py`:

```python
import unittest


class NextSessionPlanTest(unittest.TestCase):
    def test_safe_pause_strategy_creates_low_intensity_recovery_plan(self):
        from src.app.next_session_plan import build_next_session_plan

        plan = build_next_session_plan(
            {"preferred_arm": "wand_slow", "play_style": "谨慎探索型"},
            {"decision": "safe_pause"},
            {"recommended_arm": "laser_escape", "source": "memory"},
        )

        self.assertEqual(plan["recommended_arm"], "wand_slow")
        self.assertEqual(plan["scenario_focus"], "lost_target")
        self.assertEqual(plan["intensity"], "low")
        self.assertIn("慢速", plan["operator_note"])

    def test_continue_engagement_uses_personalized_arm(self):
        from src.app.next_session_plan import build_next_session_plan

        plan = build_next_session_plan(
            {"preferred_arm": "wand_hover", "play_style": "稳定观察型"},
            {"decision": "continue_engagement"},
            {"recommended_arm": "laser_escape", "source": "memory"},
        )

        self.assertEqual(plan["recommended_arm"], "laser_escape")
        self.assertEqual(plan["scenario_focus"], "approach")
        self.assertEqual(plan["intensity"], "medium")

    def test_search_again_uses_observation_plan(self):
        from src.app.next_session_plan import build_next_session_plan

        plan = build_next_session_plan(
            {"preferred_arm": "wand_slow", "play_style": "慢热试探型"},
            {"decision": "search_again"},
            {"recommended_arm": "wand_slow", "source": "default"},
        )

        self.assertEqual(plan["scenario_focus"], "empty")
        self.assertEqual(plan["intensity"], "low")
        self.assertIn("重新搜索", plan["operator_note"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_app_next_session_plan.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.app.next_session_plan'`.

- [ ] **Step 3: Implement next session plan module**

Create `src/app/next_session_plan.py`:

```python
from __future__ import annotations

from typing import Any


DEFAULT_ARM = "wand_slow"


def build_next_session_plan(
    profile: dict[str, Any],
    strategy: dict[str, Any],
    personalization: dict[str, Any],
) -> dict[str, Any]:
    decision = strategy.get("decision", "search_again")
    preferred_arm = profile.get("preferred_arm") or DEFAULT_ARM
    personalized_arm = personalization.get("recommended_arm") or preferred_arm

    if decision in {"safe_pause", "recovery_check"}:
        return {
            "recommended_arm": preferred_arm,
            "scenario_focus": "lost_target",
            "intensity": "low",
            "operator_note": "下一轮先慢速吸引，目标稳定后再靠近。",
        }
    if decision == "continue_engagement":
        return {
            "recommended_arm": personalized_arm,
            "scenario_focus": "approach",
            "intensity": "medium",
            "operator_note": "延续当前有效互动，保持节奏稳定并观察兴趣变化。",
        }
    return {
        "recommended_arm": preferred_arm,
        "scenario_focus": "empty",
        "intensity": "low",
        "operator_note": "重新搜索目标，先确认猫咪位置和兴趣再提高互动强度。",
    }
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_app_next_session_plan.py -v`

Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/app/next_session_plan.py tests/test_app_next_session_plan.py
git commit -m "feat(app): plan next interaction session"
```

### Task 4: Enhanced Report

**Files:**
- Create: `src/app/enhanced_report.py`
- Test: `tests/test_app_enhanced_report.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_app_enhanced_report.py`:

```python
import unittest


class EnhancedReportTest(unittest.TestCase):
    def test_enhanced_report_combines_report_strategy_profile_and_plan(self):
        from src.app.enhanced_report import build_enhanced_report

        report = build_enhanced_report(
            {"title": "看到了猫", "text": "基础报告"},
            {"decision": "continue_engagement", "reason": "互动质量较高。", "next_action": "继续观察。"},
            {"play_style": "主动追逐型", "summary": "参与度高。"},
            {"recommended_arm": "laser_escape", "intensity": "medium", "operator_note": "保持节奏。"},
        )

        self.assertEqual(report["title"], "Hunter 软件智能报告")
        self.assertEqual(len(report["sections"]), 4)
        self.assertIn("基础报告", report["text"])
        self.assertIn("continue_engagement", report["text"])
        self.assertIn("主动追逐型", report["text"])
        self.assertIn("laser_escape", report["text"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_app_enhanced_report.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.app.enhanced_report'`.

- [ ] **Step 3: Implement enhanced report module**

Create `src/app/enhanced_report.py`:

```python
from __future__ import annotations

from typing import Any


def build_enhanced_report(
    session_report: dict[str, Any],
    strategy: dict[str, Any],
    profile: dict[str, Any],
    next_session_plan: dict[str, Any],
) -> dict[str, Any]:
    sections = [
        {
            "title": "基础互动结果",
            "body": session_report.get("text", session_report.get("title", "无基础报告")),
        },
        {
            "title": "Agent 策略判断",
            "body": f"{strategy.get('decision')}：{strategy.get('reason')} 下一步：{strategy.get('next_action')}",
        },
        {
            "title": "猫咪画像",
            "body": f"{profile.get('play_style')}：{profile.get('summary')}",
        },
        {
            "title": "下一轮互动计划",
            "body": f"推荐玩法 {next_session_plan.get('recommended_arm')}，强度 {next_session_plan.get('intensity')}。{next_session_plan.get('operator_note')}",
        },
    ]
    text = "\n\n".join(f"## {section['title']}\n{section['body']}" for section in sections)
    return {
        "title": "Hunter 软件智能报告",
        "sections": sections,
        "text": text,
    }
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_app_enhanced_report.py -v`

Expected: PASS, 1 test.

- [ ] **Step 5: Commit**

```bash
git add src/app/enhanced_report.py tests/test_app_enhanced_report.py
git commit -m "feat(app): build enhanced intelligence report"
```

### Task 5: Software Intelligence Brief CLI

**Files:**
- Modify: `src/app/demo.py`
- Test: `tests/test_app_demo.py`

- [ ] **Step 1: Write failing CLI test**

Add this test to `tests/test_app_demo.py` inside `DemoTest`:

```python
    def test_software_intelligence_brief_returns_non_webui_agent_outputs(self):
        from src.app.demo import run_demo_entry

        result = run_demo_entry(["--software-intelligence-brief"], verbose=False)

        self.assertIn("capabilities", result)
        self.assertIn("profile", result)
        self.assertIn("strategy", result)
        self.assertIn("next_session_plan", result)
        self.assertIn("enhanced_report", result)
        self.assertIn("interaction_strategy", result["capabilities"])
        self.assertNotIn("html", result)
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m pytest tests/test_app_demo.py::DemoTest::test_software_intelligence_brief_returns_non_webui_agent_outputs -v`

Expected: FAIL because `--software-intelligence-brief` is not a recognized argument.

- [ ] **Step 3: Modify imports and parser**

In `src/app/demo.py`, add imports near the existing app imports:

```python
from src.app.cat_profile import build_cat_profile
from src.app.enhanced_report import build_enhanced_report
from src.app.interaction_strategy import build_suite_strategy
from src.app.next_session_plan import build_next_session_plan
```

Add parser flag near other product/demo flags:

```python
    parser.add_argument("--software-intelligence-brief", action="store_true")
```

- [ ] **Step 4: Add brief builder and routing**

In `run_demo_entry`, route before Web UI flags:

```python
    if args.software_intelligence_brief:
        return run_software_intelligence_brief(verbose=verbose)
```

Add this function before `run_web_ui_preview_entry`:

```python
def run_software_intelligence_brief(verbose: bool = True) -> dict[str, Any]:
    product_suite = run_product_demo_suite(verbose=False)
    artifacts = list(product_suite["artifacts"].values())
    preferences = product_suite["dashboard_preview"].get("memory_preferences", [])
    profile = build_cat_profile(artifacts, preferences)
    strategy = build_suite_strategy(artifacts)
    next_plan = build_next_session_plan(profile, strategy, product_suite["personalization_preview"])
    latest_report = artifacts[-1].get("report", {}) if artifacts else {}
    enhanced_report = build_enhanced_report(latest_report, strategy, profile, next_plan)
    brief = {
        "capabilities": [
            "interaction_strategy",
            "cat_profile",
            "next_session_plan",
            "enhanced_report",
            "personalization_policy",
        ],
        "profile": profile,
        "strategy": strategy,
        "next_session_plan": next_plan,
        "enhanced_report": enhanced_report,
    }
    if verbose:
        print({"software_intelligence_brief": brief})
    return brief
```

- [ ] **Step 5: Run CLI test to verify pass**

Run: `python -m pytest tests/test_app_demo.py::DemoTest::test_software_intelligence_brief_returns_non_webui_agent_outputs -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/app/demo.py tests/test_app_demo.py
git commit -m "feat(app): add software intelligence brief cli"
```

### Task 6: Full Verification

**Files:**
- No source edits expected unless verification reveals a defect.

- [ ] **Step 1: Run new focused tests**

Run:

```bash
python -m pytest tests/test_app_interaction_strategy.py tests/test_app_cat_profile.py tests/test_app_next_session_plan.py tests/test_app_enhanced_report.py tests/test_app_demo.py -v
```

Expected: PASS.

- [ ] **Step 2: Run full suite**

Run: `python -m pytest`

Expected: all tests pass.

- [ ] **Step 3: Run compile check**

Run: `python -m compileall src`

Expected: compile succeeds.

- [ ] **Step 4: Run demo CLI smoke check**

Run: `python -m src.app.demo --software-intelligence-brief`

Expected: prints a dict with `software_intelligence_brief`, including `profile`, `strategy`, `next_session_plan`, and `enhanced_report`.

- [ ] **Step 5: Commit any verification fixes**

If Step 1-4 required fixes, commit them:

```bash
git add src/app tests
 git commit -m "fix(app): stabilize software intelligence brief"
```

If no fixes were needed, do not create an empty commit.
