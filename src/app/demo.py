from __future__ import annotations

import argparse
from typing import Any

from src.app.config import AppConfig
from src.app.mock_api import MockHunterAPI
from src.app.orchestrator import AppOrchestrator
from src.perception.tracker import CatTracker


class NullSession:
    def get(self, *args, **kwargs):
        raise RuntimeError("real HunterAPI session is not configured")

    def post(self, *args, **kwargs):
        raise RuntimeError("real HunterAPI session is not configured")


class EmptyDetector:
    def detect(self, frame: Any) -> list[dict]:
        return []


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Hunter app loop demo.")
    parser.add_argument("--mode", choices=["mock", "real"], default="mock")
    parser.add_argument("--base-url", default=AppConfig().base_url)
    parser.add_argument("--ticks", type=int, default=10)
    return parser.parse_args(argv)


def build_api(mode: str = "mock", base_url: str | None = None):
    if mode == "real":
        from src.api_client import HunterAPI

        try:
            return HunterAPI(base_url or AppConfig().base_url)
        except ModuleNotFoundError:
            return HunterAPI(base_url or AppConfig().base_url, session=NullSession())
    return MockHunterAPI()


def build_orchestrator(mode: str = "mock", base_url: str | None = None, ticks: int = 10) -> AppOrchestrator:
    config = AppConfig(mode=mode, base_url=base_url or AppConfig().base_url)
    api = build_api(mode=config.mode, base_url=config.base_url)
    detector = EmptyDetector()
    tracker = CatTracker(frame_size=(640, 480))
    return AppOrchestrator(api=api, detector=detector, tracker=tracker, config=config)


def run_demo(argv: list[str] | None = None, verbose: bool = True) -> list[dict]:
    args = parse_args(argv)
    orchestrator = build_orchestrator(mode=args.mode, base_url=args.base_url, ticks=args.ticks)
    snapshots = []
    for _ in range(args.ticks):
        state = orchestrator.tick().to_dict()
        snapshots.append(state)
        if verbose:
            print(state)
    return snapshots


if __name__ == "__main__":
    run_demo(verbose=True)
