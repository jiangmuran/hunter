"""
所有对 Robot Control Unit 的调用都经过这里。
上层模块只 import HunterAPI，不直接写 URL 或 requests。
"""

API_BASE = "http://192.168.0.170:8000"


class HunterAPI:
    def __init__(self, base_url: str = API_BASE, session=None):
        self.base = base_url.rstrip("/")
        if session is None:
            import requests

            session = requests.Session()
        self.session = session

    # ── 摄像头 ────────────────────────────────────────────
    def snapshot(self):
        """返回当前帧 BGR numpy 数组"""
        import cv2
        import numpy as np

        resp = self.session.get(f"{self.base}/api/camera/snapshot.jpg", timeout=5)
        resp.raise_for_status()
        arr = np.frombuffer(resp.content, np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)

    def stream_url(self) -> str:
        return f"{self.base}/api/camera/stream.mjpg"

    # ── 运动控制 ──────────────────────────────────────────
    def cmd(self, action: str) -> dict:
        resp = self.session.post(f"{self.base}/api/cmd/{action}", timeout=5)
        resp.raise_for_status()
        return resp.json()

    def move(self, direction: str):
        """direction: forward / backward / left / right / stop"""
        return self.cmd(direction)

    def rotate(self, clockwise: bool = True):
        return self.cmd("rotate_cw" if clockwise else "rotate_ccw")

    def stop(self):
        return self.cmd("stop")

    def emergency(self):
        return self.cmd("emergency")

    # ── 音效 ─────────────────────────────────────────────
    def play_cat_sound(self, n: int = 1):
        """n: 1–4"""
        return self.cmd(f"cat{n}")

    # ── 录音 ─────────────────────────────────────────────
    def record_start(self):
        resp = self.session.post(f"{self.base}/api/audio/rec/start", timeout=5)
        resp.raise_for_status()
        return resp.json()

    def record_stop(self):
        resp = self.session.post(f"{self.base}/api/audio/rec/stop", timeout=5)
        resp.raise_for_status()
        return resp.json()

    def play_wav(self, filename: str):
        resp = self.session.post(
            f"{self.base}/api/audio/play",
            json={"filename": filename},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()

    # ── 状态 ─────────────────────────────────────────────
    def state(self) -> dict:
        resp = self.session.get(f"{self.base}/api/state", timeout=5)
        resp.raise_for_status()
        return resp.json()

    def health(self) -> dict:
        resp = self.session.get(f"{self.base}/api/health", timeout=5)
        resp.raise_for_status()
        return resp.json()
