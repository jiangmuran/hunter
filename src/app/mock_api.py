from time import time
from typing import Any


class MockFrame:
    shape = (480, 640, 3)


class MockHunterAPI:
    def __init__(self, healthy: bool = True):
        self.healthy = healthy
        self.command_history: list[dict[str, Any]] = []
        self.recording = False
        self.rec_path: str | None = None

    def snapshot(self) -> MockFrame:
        return MockFrame()

    def stream_url(self) -> str:
        return "mock://camera/stream.mjpg"

    def cmd(self, action: str) -> dict:
        entry = {"t": int(time() * 1000), "action": action}
        self.command_history.append(entry)
        return {"ok": True, "action": action, "mode": "mock"}

    def move(self, direction: str):
        return self.cmd(direction)

    def rotate(self, clockwise: bool = True):
        return self.cmd("rotate_cw" if clockwise else "rotate_ccw")

    def stop(self):
        return self.cmd("stop")

    def emergency(self):
        return self.cmd("emergency")

    def play_cat_sound(self, n: int = 1):
        return self.cmd(f"cat{n}")

    def record_start(self):
        self.recording = True
        self.rec_path = "mock_recording.wav"
        return {"ok": True, "path": self.rec_path}

    def record_stop(self):
        self.recording = False
        return {"ok": True, "path": self.rec_path}

    def play_wav(self, filename: str):
        return {"ok": True, "filename": filename}

    def state(self) -> dict:
        last = self.command_history[-1]["action"].upper() if self.command_history else "STOP"
        return {
            "state": {
                "ultra": {
                    "1": {"distance_mm": None, "has_obstacle": False, "t": None},
                    "2": {"distance_mm": None, "has_obstacle": False, "t": None},
                    "3": {"distance_mm": None, "has_obstacle": False, "t": None},
                },
                "modes": {"obstacle": False, "ultra_report": False, "emergency": False},
                "speed": None,
                "last_action": last,
                "recording": self.recording,
                "rec_path": self.rec_path,
            },
            "logs": [
                {"t": item["t"], "kind": "cmd", "line": f"mock: {item['action']}"}
                for item in self.command_history[-80:]
            ],
        }

    def health(self) -> dict:
        return {
            "modules": {
                "mock": {"ok": self.healthy, "detail": "mock hardware"},
            },
            "system": {"clients": 0},
        }
