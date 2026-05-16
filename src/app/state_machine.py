from dataclasses import replace

from src.app.config import AppConfig
from src.app.state import AppState, RuntimeState


class StateMachine:
    def __init__(self, config: AppConfig | None = None):
        self.config = config or AppConfig()

    def transition(
        self,
        state: AppState,
        target: dict | None,
        healthy: bool = True,
        emergency: bool = False,
        error: str | None = None,
    ) -> AppState:
        if emergency:
            return replace(
                state,
                current=RuntimeState.EMERGENCY_STOP,
                target=target,
                healthy=healthy,
                error=error,
            )
        if not healthy or error:
            return replace(
                state,
                current=RuntimeState.ERROR,
                target=target,
                healthy=False,
                error=error,
            )
        if target and int(target.get("missing_count", 0)) >= self.config.missing_limit:
            return replace(state, current=RuntimeState.LOST_TARGET, target=target, healthy=True, error=None)
        if not target:
            return replace(state, current=RuntimeState.SCANNING, target=None, healthy=True, error=None)

        size_ratio = float(target.get("size_ratio", 0.0))
        offset = float(target.get("center_offset_x", 0.0))
        if size_ratio >= self.config.stop_size_ratio:
            next_state = RuntimeState.AT_STOP_DISTANCE
        elif abs(offset) > self.config.align_threshold:
            next_state = RuntimeState.ALIGNING
        else:
            next_state = RuntimeState.APPROACHING
        return replace(state, current=next_state, target=target, healthy=True, error=None)
