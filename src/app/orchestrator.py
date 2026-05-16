from __future__ import annotations

from dataclasses import replace
from typing import Any

from src.app.config import AppConfig
from src.app.events import AppEvent, EventKind
from src.app.state import AppState
from src.app.state_machine import StateMachine
from src.hunt.motion_generator import MotionGenerator


class AppOrchestrator:
    def __init__(
        self,
        api: Any,
        detector: Any,
        tracker: Any,
        config: AppConfig | None = None,
        state_machine: StateMachine | None = None,
        motion_generator: MotionGenerator | None = None,
    ):
        self.api = api
        self.detector = detector
        self.tracker = tracker
        self.config = config or AppConfig()
        self.state_machine = state_machine or StateMachine(self.config)
        self.motion_generator = motion_generator or MotionGenerator(self.config)
        self.state = AppState()
        self.events: list[AppEvent] = []

    def tick(self) -> AppState:
        tick = self.state.tick + 1
        try:
            healthy = self._is_healthy(self.api.health())
            frame = self.api.snapshot()
            detections = self.detector.detect(frame)
            target = self.tracker.update(detections)
            next_state = self.state_machine.transition(replace(self.state, tick=tick), target, healthy=healthy)
            self._record_state(next_state)
            decision = self.motion_generator.decide(next_state, target)
            self._dispatch(decision.action)
            self.state = replace(next_state, last_action=decision.action)
            self._record_command(decision.action, decision.reason)
            return self.state
        except Exception as exc:
            next_state = self.state_machine.transition(
                replace(self.state, tick=tick), None, healthy=False, error=str(exc)
            )
            self._dispatch("stop")
            self.state = replace(next_state, last_action="stop")
            self.events.append(AppEvent(EventKind.ERROR, tick, str(exc)))
            self._record_command("stop", "error")
            return self.state

    def _dispatch(self, action: str) -> None:
        if action == "forward":
            self.api.move("forward")
        elif action == "rotate_cw":
            self.api.rotate(clockwise=True)
        elif action == "rotate_ccw":
            self.api.rotate(clockwise=False)
        else:
            self.api.stop()

    def _record_state(self, state: AppState) -> None:
        self.events.append(AppEvent(EventKind.STATE, state.tick, state.current.value, state.to_dict()))

    def _record_command(self, action: str, reason: str) -> None:
        self.events.append(AppEvent(EventKind.COMMAND, self.state.tick, action, {"reason": reason}))

    def _is_healthy(self, health: dict[str, Any]) -> bool:
        modules = health.get("modules", {})
        return all(bool(module.get("ok")) for module in modules.values())
