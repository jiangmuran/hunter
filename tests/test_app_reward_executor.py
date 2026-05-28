from src.app.mock_api import MockHunterAPI
from src.app.reward_executor import RewardExecutor


def test_reward_executor_dispenses_when_policy_allows():
    api = MockHunterAPI()
    result = RewardExecutor(api).maybe_reward({"outcome": "caught", "catch_success": True})

    assert result["dispensed"] is True
    assert api.command_history[-1]["action"].startswith("treat:")


def test_reward_executor_does_not_dispense_for_lost_target():
    api = MockHunterAPI()
    result = RewardExecutor(api).maybe_reward({"outcome": "lost_target", "catch_success": False})

    assert result["dispensed"] is False
    assert api.command_history == []
