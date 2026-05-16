from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RuntimeState(Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    TARGET_ACQUIRED = "target_acquired"
    ALIGNING = "aligning"
    APPROACHING = "approaching"
    AT_STOP_DISTANCE = "at_stop_distance"
    LOST_TARGET = "lost_target"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"


@dataclass
class AppState:
    current: RuntimeState = RuntimeState.IDLE
    tick: int = 0
    last_action: str | None = None
    target: dict[str, Any] | None = None
    healthy: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.current.value,
            "tick": self.tick,
            "last_action": self.last_action,
            "target": self.target,
            "healthy": self.healthy,
            "error": self.error,
        }
