from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from src.app.cat_profile import build_cat_profile
from src.app.config import AppConfig
from src.app.daily_diary import build_daily_diary_from_sessions
from src.app.dashboard_preview import build_dashboard_preview
from src.app.enhanced_report import build_enhanced_report
from src.app.interaction_strategy import build_suite_strategy
from src.app.mock_api import MockHunterAPI
from src.app.mvp_milestone import build_mvp_milestone
from src.app.next_session_plan import build_next_session_plan
from src.app.orchestrator import AppOrchestrator
from src.app.personalization_policy import build_personalization_preview
from src.app.session_artifact import build_session_artifact
from src.app.session_memory import apply_session_memory_update, memory_preferences, session_memory_update
from src.app.session_report import build_session_report
from src.app.session_summary import summarize_session
from src.app.surprise_entropy import build_surprise_entropy_preview
from src.software.perception.tracker import CatTracker


MOCK_SCENARIO_TICKS = {
    "empty": 3,
    "approach": 4,
    "lost_target": 7,
    "error": 3,
}


class NullSession:
    def get(self, *args, **kwargs):
        raise RuntimeError("real HunterAPI session is not configured")

    def post(self, *args, **kwargs):
        raise RuntimeError("real HunterAPI session is not configured")


class EmptyDetector:
    def detect(self, frame: Any) -> list[dict]:
        return []


class SequenceDetector:
    def __init__(self, frames: list[list[dict]]):
        self.frames = frames
        self.index = 0

    def detect(self, frame: Any) -> list[dict]:
        if not self.frames:
            return []
        index = min(self.index, len(self.frames) - 1)
        self.index += 1
        return self.frames[index]


class FailingDetector:
    def detect(self, frame: Any) -> list[dict]:
        raise RuntimeError("mock detector failed")


def detection(cx: float, cy: float, w: float, h: float, conf: float = 0.8) -> dict:
    return {
        "bbox": (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
        "conf": conf,
        "cx": cx,
        "cy": cy,
        "w": w,
        "h": h,
    }


def build_detector(scenario: str = "empty"):
    if scenario == "approach":
        return SequenceDetector([
            [],
            [detection(cx=500, cy=240, w=120, h=120)],
            [detection(cx=320, cy=240, w=160, h=160)],
            [detection(cx=320, cy=240, w=360, h=360)],
        ])
    if scenario == "lost_target":
        return SequenceDetector([
            [detection(cx=320, cy=240, w=160, h=160)],
            [],
            [],
            [],
            [],
        ])
    if scenario == "error":
        return FailingDetector()
    return EmptyDetector()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Hunter app loop demo.")
    parser.add_argument("--mode", choices=["mock", "real"], default="mock")
    parser.add_argument("--base-url", default=AppConfig().base_url)
    parser.add_argument("--ticks", type=int, default=10)
    parser.add_argument("--scenario", choices=["empty", "approach", "lost_target", "error", "all"], default="empty")
    parser.add_argument("--include-memory-update", action="store_true")
    parser.add_argument("--product-suite", action="store_true")
    parser.add_argument("--software-mvp-acceptance", action="store_true")
    parser.add_argument("--software-intelligence-brief", action="store_true")
    parser.add_argument("--surprise-entropy-preview", action="store_true")
    parser.add_argument("--web-ui-preview", action="store_true")
    parser.add_argument("--web-ui-interactive", action="store_true")
    parser.add_argument("--web-ui-output")
    return parser.parse_args(argv)


def build_api(mode: str = "mock", base_url: str | None = None):
    if mode == "real":
        from src.software.api_client import HunterAPI

        try:
            return HunterAPI(base_url or AppConfig().base_url)
        except ModuleNotFoundError:
            return HunterAPI(base_url or AppConfig().base_url, session=NullSession())
    return MockHunterAPI()


def build_orchestrator(
    mode: str = "mock",
    base_url: str | None = None,
    ticks: int = 10,
    scenario: str = "empty",
) -> AppOrchestrator:
    config = AppConfig(mode=mode, base_url=base_url or AppConfig().base_url)
    api = build_api(mode=config.mode, base_url=config.base_url)
    detector = build_detector(scenario if config.mode == "mock" else "empty")
    tracker = CatTracker(frame_size=(640, 480))
    return AppOrchestrator(api=api, detector=detector, tracker=tracker, config=config)


def run_demo_session(argv: list[str] | None = None, verbose: bool = True) -> dict:
    args = parse_args(argv)
    if args.scenario == "all":
        raise ValueError("run_demo_session only runs one scenario; use run_demo_entry or run_demo_suite for 'all'")
    orchestrator = build_orchestrator(
        mode=args.mode,
        base_url=args.base_url,
        ticks=args.ticks,
        scenario=args.scenario,
    )
    states = []
    for _ in range(args.ticks):
        state = orchestrator.tick().to_dict()
        states.append(state)
        if verbose:
            print(state)
    events = [event.to_dict() for event in orchestrator.events]
    summary = summarize_session(states, events)
    report = build_session_report(summary)
    memory_update = session_memory_update(summary) if args.include_memory_update else None
    if verbose:
        print({"summary": summary})
        print({"report": report})
        if args.include_memory_update:
            print({"memory_update": memory_update})
    session = {"states": states, "events": events, "summary": summary, "report": report}
    if args.include_memory_update:
        session["memory_update"] = memory_update
    return session


def run_demo_suite(verbose: bool = True, include_memory_update: bool = True) -> dict:
    sessions = {}
    # Run the acceptance scenarios in one fresh orchestrator each so tracker state never leaks across cases.
    for scenario, ticks in MOCK_SCENARIO_TICKS.items():
        argv = ["--mode", "mock", "--scenario", scenario, "--ticks", str(ticks)]
        if include_memory_update:
            argv.append("--include-memory-update")
        if verbose:
            print({"scenario": scenario})
        sessions[scenario] = run_demo_session(argv, verbose=verbose)

    outcome_counts = {}
    for session in sessions.values():
        outcome = session["report"]["outcome"]
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

    suite = {"sessions": sessions, "outcome_counts": outcome_counts}
    suite["milestone"] = build_mvp_milestone(suite)
    if verbose:
        print({"suite": {"outcome_counts": outcome_counts, "milestone": suite["milestone"]}})
    return suite


def run_product_demo_suite(
    verbose: bool = True,
    store: Any | None = None,
    memory_box: Any | None = None,
) -> dict:
    suite = run_demo_suite(verbose=verbose, include_memory_update=True)
    artifacts = {}
    memory_updates = []
    for scenario, session in suite["sessions"].items():
        artifact = build_session_artifact(
            session,
            mode="mock",
            scenario=scenario,
        )
        artifacts[scenario] = artifact
        if store is not None:
            store.save(artifact)
        if memory_box is not None:
            applied_update = apply_session_memory_update(session["summary"], memory_box)
            if applied_update is not None:
                memory_updates.append(applied_update)

    preferences = memory_preferences(memory_box) if memory_box is not None else []
    dashboard_preview = build_dashboard_preview(
        list(artifacts.values()),
        memory_preferences=preferences,
        milestone=suite["milestone"],
    )
    daily_diary = build_daily_diary_from_sessions(list(artifacts.values()))
    personalization_preview = build_personalization_preview(memory_box)
    product_suite = {
        **suite,
        "artifacts": artifacts,
        "dashboard_preview": dashboard_preview,
        "daily_diary": daily_diary,
        "memory_updates": memory_updates,
        "personalization_preview": personalization_preview,
    }
    if verbose:
        print({"dashboard_preview": dashboard_preview})
        print({"daily_diary": daily_diary})
        print({"personalization_preview": personalization_preview})
    return product_suite


def run_demo_entry(argv: list[str] | None = None, verbose: bool = True) -> dict:
    args = parse_args(argv)
    if args.software_intelligence_brief:
        return run_software_intelligence_brief(verbose=verbose)
    if args.surprise_entropy_preview:
        return run_surprise_entropy_preview(verbose=verbose)
    if args.web_ui_preview or args.web_ui_interactive:
        return run_web_ui_preview_entry(args.web_ui_output, verbose=verbose)
    if args.product_suite:
        return run_product_demo_suite(verbose=verbose)
    if args.software_mvp_acceptance:
        return run_software_mvp_acceptance(verbose=verbose)
    if args.mode == "mock" and args.scenario == "all":
        return run_demo_suite(verbose=verbose, include_memory_update=args.include_memory_update)
    return run_demo_session(argv, verbose=verbose)


def run_software_intelligence_brief(verbose: bool = True) -> dict[str, Any]:
    product_suite = run_product_demo_suite(verbose=False)
    artifacts = list(product_suite["artifacts"].values())
    preferences = product_suite["dashboard_preview"].get("memory_preferences", [])
    profile = build_cat_profile(artifacts, preferences)
    strategy = build_suite_strategy(artifacts)
    next_plan = build_next_session_plan(profile, strategy, product_suite["personalization_preview"])
    entropy = build_surprise_entropy_preview(
        profile,
        strategy,
        product_suite["personalization_preview"],
        recent_outcomes=_recent_outcomes(artifacts),
        recent_actions=_recent_actions(artifacts),
    )
    representative_artifact = _representative_brief_artifact(artifacts)
    representative_report = representative_artifact.get("report", {}) if representative_artifact else {}
    enhanced_report = build_enhanced_report(representative_report, strategy, profile, next_plan)
    brief = {
        "capabilities": [
            "interaction_strategy",
            "cat_profile",
            "next_session_plan",
            "enhanced_report",
            "personalization_policy",
            "surprise_entropy_engine",
        ],
        "profile": profile,
        "strategy": strategy,
        "next_session_plan": next_plan,
        "enhanced_report": enhanced_report,
        "surprise_entropy": entropy,
    }
    if verbose:
        print({"software_intelligence_brief": brief})
    return brief


def run_surprise_entropy_preview(verbose: bool = True) -> dict[str, Any]:
    product_suite = run_product_demo_suite(verbose=False)
    artifacts = list(product_suite["artifacts"].values())
    preferences = product_suite["dashboard_preview"].get("memory_preferences", [])
    profile = build_cat_profile(artifacts, preferences)
    strategy = build_suite_strategy(artifacts)
    entropy = build_surprise_entropy_preview(
        profile,
        strategy,
        product_suite["personalization_preview"],
        recent_outcomes=_recent_outcomes(artifacts),
        recent_actions=_recent_actions(artifacts),
    )
    if verbose:
        print({"surprise_entropy_preview": entropy})
    return entropy


def _recent_outcomes(artifacts: list[dict[str, Any]]) -> list[str]:
    return [artifact.get("report", {}).get("outcome", "") for artifact in artifacts]


def _recent_actions(artifacts: list[dict[str, Any]]) -> list[str]:
    actions = []
    for artifact in artifacts:
        report = artifact.get("report", {}) if isinstance(artifact.get("report", {}), dict) else {}
        outcome = report.get("outcome")
        if outcome == "success":
            actions.append("wand_slow_sweep")
        elif outcome == "lost_target":
            actions.append("laser_escape_short")
        elif outcome in {"no_target", "error"}:
            actions.append("pause_observe")
    return actions


def _representative_brief_artifact(artifacts: list[dict[str, Any]]) -> dict[str, Any] | None:
    for preferred_outcome in ("success", "partial", "lost_target", "no_target", "error"):
        for artifact in artifacts:
            report = artifact.get("report", {}) if isinstance(artifact.get("report", {}), dict) else {}
            if report.get("outcome") == preferred_outcome:
                return artifact
    return artifacts[-1] if artifacts else None


def run_web_ui_preview_entry(output_path: str | None = None, verbose: bool = True) -> dict[str, Any]:
    from src.app.web_ui import run_web_ui_preview

    html = run_web_ui_preview(verbose=False)
    result = {"html": html, "output_path": None}
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        result["output_path"] = str(path)
        if verbose:
            print({"web_ui_output": str(path)})
    elif verbose:
        print(html)
    return result


def run_demo(argv: list[str] | None = None, verbose: bool = True) -> list[dict]:
    return run_demo_session(argv, verbose)["states"]


def run_software_mvp_acceptance(verbose: bool = True) -> dict[str, Any]:
    product_suite = run_product_demo_suite(verbose=verbose)
    milestone = product_suite["milestone"]
    acceptance = {
        "name": "software_mvp_acceptance",
        "ready_for_hardware_integration": milestone["complete"],
        "total_sessions": product_suite["dashboard_preview"]["total_sessions"],
        "outcome_counts": product_suite["outcome_counts"],
        "capabilities": [
            *milestone["completed_capabilities"],
            "session artifacts",
            "dashboard_preview",
            "daily_diary",
            "memory update adapter",
            "personalization_policy",
        ],
        "personalization": product_suite["personalization_preview"],
        "remaining_for_real_mvp": milestone["next_phase"],
    }
    if verbose:
        print({"software_mvp_acceptance": acceptance})
    return acceptance


def run_personalized_demo_acceptance(memory_box: Any, verbose: bool = True) -> dict[str, Any]:
    product_suite = run_product_demo_suite(verbose=verbose, memory_box=memory_box)
    personalization = product_suite["personalization_preview"]
    acceptance = {
        "recommended_arm": personalization["recommended_arm"],
        "source": personalization["source"],
        "expected_reward": personalization["expected_reward"],
        "memory_updates": len(product_suite["memory_updates"]),
        "preferences": personalization["preferences"],
    }
    if verbose:
        print({"personalized_demo_acceptance": acceptance})
    return acceptance


if __name__ == "__main__":
    run_demo_entry(verbose=True)
