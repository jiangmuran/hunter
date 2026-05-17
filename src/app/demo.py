from __future__ import annotations

import argparse
from typing import Any

from src.app.config import AppConfig
from src.app.dashboard_preview import build_dashboard_preview
from src.app.mock_api import MockHunterAPI
from src.app.mvp_milestone import build_mvp_milestone
from src.app.orchestrator import AppOrchestrator
from src.app.session_artifact import build_session_artifact
from src.app.session_memory import memory_preferences, session_memory_update
from src.app.session_report import build_session_report
from src.app.session_summary import summarize_session
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
    for scenario, session in suite["sessions"].items():
        artifact = build_session_artifact(
            session,
            mode="mock",
            scenario=scenario,
        )
        artifacts[scenario] = artifact
        if store is not None:
            store.save(artifact)

    preferences = memory_preferences(memory_box) if memory_box is not None else []
    dashboard_preview = build_dashboard_preview(
        list(artifacts.values()),
        memory_preferences=preferences,
        milestone=suite["milestone"],
    )
    product_suite = {**suite, "artifacts": artifacts, "dashboard_preview": dashboard_preview}
    if verbose:
        print({"dashboard_preview": dashboard_preview})
    return product_suite


def run_demo_entry(argv: list[str] | None = None, verbose: bool = True) -> dict:
    args = parse_args(argv)
    if args.mode == "mock" and args.scenario == "all":
        return run_demo_suite(verbose=verbose, include_memory_update=args.include_memory_update)
    return run_demo_session(argv, verbose=verbose)


def run_demo(argv: list[str] | None = None, verbose: bool = True) -> list[dict]:
    return run_demo_session(argv, verbose)["states"]


if __name__ == "__main__":
    run_demo_entry(verbose=True)
