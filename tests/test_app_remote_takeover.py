from src.app.mock_api import MockHunterAPI
from src.app.remote_takeover import RemoteTakeover


def test_remote_takeover_requires_operator_token():
    api = MockHunterAPI()
    result = RemoteTakeover(api, operator_token="demo").dispatch("forward", token="wrong")

    assert result["ok"] is False
    assert result["reason"] == "unauthorized"
    assert api.command_history == []


def test_remote_takeover_dispatches_allowed_command():
    api = MockHunterAPI()
    result = RemoteTakeover(api, operator_token="demo").dispatch("stop", token="demo")

    assert result["ok"] is True
    assert api.command_history[-1]["action"].startswith("remote:stop")


def test_remote_takeover_rejects_unknown_command():
    api = MockHunterAPI()
    result = RemoteTakeover(api, operator_token="demo").dispatch("delete_everything", token="demo")

    assert result["ok"] is False
    assert result["reason"] == "unsupported_command"
