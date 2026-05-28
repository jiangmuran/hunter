from __future__ import annotations

from typing import Any

from src.app.activity_sensing import build_activity_score
from src.app.audio_emotion import classify_audio_emotion
from src.app.hardware_contract import build_hardware_contract_report
from src.app.play_executor import PlayExecutor


class HardwarePlugRuntime:
    def __init__(self, api: Any):
        self.api = api
        self.play_executor = PlayExecutor(api)

    def tick(self, play_action: str = "wand_hover") -> dict[str, Any]:
        contract = build_hardware_contract_report(self.api)
        if not contract["ready"]:
            return {"contract_ready": False, "missing": contract["missing"], "contract": contract}
        frame = self.api.snapshot()
        audio_features = self.api.capture_audio_features()
        activity_sample = self.api.activity_sample()
        activity = build_activity_score(target={"visible": frame is not None}, sample=activity_sample)
        audio_emotion = classify_audio_emotion(audio_features)
        play = self.play_executor.execute(play_action, activity_level=activity["level"])
        water = self.api.water_state()
        return {
            "contract_ready": True,
            "activity": activity,
            "audio_emotion": {"emotion": audio_emotion["label"], **audio_emotion},
            "play": play,
            "water": water,
            "contract": contract,
        }
