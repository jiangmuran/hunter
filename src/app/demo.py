from __future__ import annotations

import argparse
from typing import Any

from src.app.config import AppConfig
from src.app.mock_api import MockHunterAPI
from src.app.orchestrator import AppOrchestrator
from src.app.session_report import build_session_report
from src.app.session_summary import summarize_session
from src.software.perception.tracker import CatTracker


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
    parser.add_argument("--scenario", choices=["empty", "approach", "lost_target", "error"], default="empty")
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
    if verbose:
        print({"summary": summary})
        print({"report": report})
    return {"states": states, "events": events, "summary": summary, "report": report}


def run_demo(argv: list[str] | None = None, verbose: bool = True) -> list[dict]:
    return run_demo_session(argv, verbose)["states"]


if __name__ == "__main__":
    run_demo_session(verbose=True)
