from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Any


class EventKind(Enum):
    PERCEPTION = "perception"
    STATE = "state"
    COMMAND = "command"
    HEALTH = "health"
    ERROR = "error"


@dataclass(frozen=True)
class AppEvent:
    kind: EventKind
    tick: int
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "tick": self.tick,
            "message": self.message,
            "payload": self.payload,
            "ts": self.ts,
        }
