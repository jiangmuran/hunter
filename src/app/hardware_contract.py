from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HardwareCapabilityStatus:
    capability: str
    method: str
    ready: bool

    def to_dict(self) -> dict[str, Any]:
        return {"capability": self.capability, "method": self.method, "ready": self.ready}


REQUIRED_HARDWARE_METHODS = {
    "camera_snapshot": "snapshot",
    "audio_features": "capture_audio_features",
    "activity_sample": "activity_sample",
    "play_actuator": "execute_play_action",
    "reward_actuator": "dispense_treat",
    "water_sensor": "water_state",
    "remote_command": "remote_command",
}


def build_hardware_contract_report(api: Any) -> dict[str, Any]:
    capabilities = [
        HardwareCapabilityStatus(capability, method, callable(getattr(api, method, None)))
        for capability, method in REQUIRED_HARDWARE_METHODS.items()
    ]
    missing = [item.method for item in capabilities if not item.ready]
    return {
        "ready": not missing,
        "missing": missing,
        "capabilities": [item.to_dict() for item in capabilities],
    }
