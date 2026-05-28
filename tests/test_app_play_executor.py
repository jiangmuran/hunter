from src.app.mock_api import MockHunterAPI
from src.app.play_executor import PlayExecutor, build_play_command


def test_build_play_command_maps_known_action_to_safe_duration():
    command = build_play_command("wand_fast", activity_level="high")

    assert command["action"] == "wand_fast"
    assert command["intensity"] == "medium"
    assert command["duration_ms"] <= 1500
    assert command["safety"] == "bounded"


def test_play_executor_dispatches_to_hardware_contract():
    api = MockHunterAPI()
    result = PlayExecutor(api).execute("laser_escape", activity_level="medium")

    assert result["ok"] is True
    assert api.command_history[-1]["action"].startswith("play:laser_escape")
