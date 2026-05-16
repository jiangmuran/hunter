from dataclasses import dataclass

from src.app.config import AppConfig
from src.app.state import AppState, RuntimeState


@dataclass(frozen=True)
class MotionDecision:
    action: str
    reason: str


class MotionGenerator:
    def __init__(self, config: AppConfig | None = None):
        self.config = config or AppConfig()

    def decide(self, state: AppState, target: dict | None) -> MotionDecision:
        if state.current in {
            RuntimeState.ERROR,
            RuntimeState.EMERGENCY_STOP,
            RuntimeState.LOST_TARGET,
            RuntimeState.AT_STOP_DISTANCE,
        }:
            return MotionDecision("stop", state.current.value)
        if target is None:
            return MotionDecision("stop", "no_target")

        offset = float(target.get("center_offset_x", 0.0))
        if state.current == RuntimeState.ALIGNING:
            if offset > self.config.align_threshold:
                return MotionDecision("rotate_cw", "target_right")
            if offset < -self.config.align_threshold:
                return MotionDecision("rotate_ccw", "target_left")
            return MotionDecision("stop", "aligned")
        if state.current == RuntimeState.APPROACHING:
            return MotionDecision("forward", "target_centered")
        return MotionDecision("stop", "idle")
