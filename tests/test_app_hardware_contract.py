from src.app.hardware_contract import build_hardware_contract_report
from src.app.mock_api import MockHunterAPI
from src.software.api_client import HunterAPI


def test_mock_api_satisfies_robot_side_hardware_contract():
    api = MockHunterAPI()
    report = build_hardware_contract_report(api)

    assert report["ready"] is True
    assert report["missing"] == []
    assert {item["capability"] for item in report["capabilities"]} == {
        "camera_snapshot",
        "audio_features",
        "activity_sample",
        "play_actuator",
        "reward_actuator",
        "water_sensor",
        "remote_command",
    }




class RecordingSession:
    def __init__(self):
        self.calls = []

    def get(self, url, timeout=2):
        self.calls.append(("GET", url, None, timeout))
        return Response({"ok": True, "level_mm": 40})

    def post(self, url, json=None, timeout=2):
        self.calls.append(("POST", url, json, timeout))
        return Response({"ok": True, "payload": json})


class Response:
    content = b""

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_hunter_api_exposes_hardware_plug_endpoints():
    session = RecordingSession()
    api = HunterAPI("http://hunter.local", session=session)

    api.capture_audio_features()
    api.activity_sample()
    api.execute_play_action("wand_fast", intensity="high", duration_ms=900)
    api.dispense_treat(grams=1.5, reason="catch")
    api.water_state()
    api.remote_command("stop")

    assert ("GET", "http://hunter.local/api/audio/features", None, 5) in session.calls
    assert ("GET", "http://hunter.local/api/activity/sample", None, 5) in session.calls
    assert ("POST", "http://hunter.local/api/play/action", {"action": "wand_fast", "intensity": "high", "duration_ms": 900}, 5) in session.calls
    assert ("POST", "http://hunter.local/api/reward/treat", {"grams": 1.5, "reason": "catch"}, 5) in session.calls
    assert ("GET", "http://hunter.local/api/water/state", None, 5) in session.calls
    assert ("POST", "http://hunter.local/api/remote/command", {"command": "stop"}, 5) in session.calls
