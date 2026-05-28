from __future__ import annotations

from typing import Any


ALLOWED_REMOTE_COMMANDS = {"forward", "rotate_cw", "rotate_ccw", "stop", "emergency", "play_sound"}


class RemoteTakeover:
    def __init__(self, api: Any, operator_token: str):
        self.api = api
        self.operator_token = operator_token

    def dispatch(self, command: str, token: str, **params: Any) -> dict[str, Any]:
        if token != self.operator_token:
            return {"ok": False, "reason": "unauthorized", "command": command}
        if command not in ALLOWED_REMOTE_COMMANDS:
            return {"ok": False, "reason": "unsupported_command", "command": command}
        response = self.api.remote_command(command, **params)
        return {"ok": bool(response.get("ok", True)), "command": command, "response": response}
