from __future__ import annotations

import threading
import time

from src.api_client import HunterAPI
from src.perception import CatDetector

# ── 可调参数 ────────────────────────────────────────────────────────────────
TURN_THRESHOLD = 0.15   # 猫中心偏离画面中心超过帧宽的 15% → 先旋转对齐
STOP_BOX_RATIO = 0.38   # 猫 bbox 高度 / 帧高超过此值 → 到达制动距离，停车
LOOP_INTERVAL  = 0.12   # 控制节拍（秒），≥ hold-to-move 的 100ms 间隔
MISSING_LIMIT  = 4      # 连续丢帧超过此次数 → 主动停车等待
# ───────────────────────────────────────────────────────────────────────────


class CatChaser:
    """
    识别到猫就往猫的方向走，留制动空间停车。

    控制逻辑（每帧）：
        1. 从摄像头取最新帧，跑 YOLO 检测猫。
        2. 无猫：连续丢帧 MISSING_LIMIT 次后停车，等待重新发现。
        3. 有猫：取面积最大（最近）的一只。
           a. bbox 高度 ≥ STOP_BOX_RATIO × 帧高 → 制动距离到，停车。
           b. 猫横向偏离 > TURN_THRESHOLD × 帧宽 → 原地旋转对齐。
           c. 对齐后 → 前进。
        4. 重复，直到 stop() 或 duration 超时。

    使用示例：
        api     = HunterAPI()
        chaser  = CatChaser(api)
        chaser.run(duration=60)          # 阻塞运行 60 秒

        # 或非阻塞：
        t = threading.Thread(target=chaser.run, daemon=True)
        t.start()
        ...
        chaser.stop()
    """

    def __init__(self, api: HunterAPI, detector: CatDetector | None = None):
        self.api = api
        self.detector = detector or CatDetector()
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """通知运行循环退出。"""
        self._stop_event.set()

    def run(self, duration: float | None = None) -> None:
        """
        阻塞式追猫循环。
        duration: 最长运行秒数；None = 一直运行到 stop()。
        """
        self._stop_event.clear()
        deadline = time.time() + duration if duration is not None else None
        missing = 0

        try:
            while not self._stop_event.is_set():
                if deadline is not None and time.time() >= deadline:
                    break

                # ── 1. 取帧 ──────────────────────────────────────────────
                try:
                    frame = self.api.snapshot()
                except Exception as e:
                    print(f"[chaser] snapshot error: {e}")
                    time.sleep(LOOP_INTERVAL)
                    continue

                h, w = frame.shape[:2]

                # ── 2. 检测 ──────────────────────────────────────────────
                dets = self.detector.detect(frame)

                if not dets:
                    missing += 1
                    if missing >= MISSING_LIMIT:
                        self.api.stop()
                        print("[chaser] cat lost — waiting")
                    time.sleep(LOOP_INTERVAL)
                    continue

                missing = 0
                # 取面积最大的猫（通常是最近的）
                cat = max(dets, key=lambda d: d["w"] * d["h"])

                # ── 3a. 制动距离检查 ──────────────────────────────────────
                # 猫的 bbox 高度占帧高的比例反映了距离：越大越近。
                box_ratio = cat["h"] / h
                if box_ratio >= STOP_BOX_RATIO:
                    self.api.stop()
                    print(
                        f"[chaser] braking — cat at {box_ratio:.0%} of frame height"
                    )
                    time.sleep(LOOP_INTERVAL)
                    continue

                # ── 3b/3c. 转向或前进 ────────────────────────────────────
                # dx 归一化到 [-0.5, +0.5]：负 = 猫在左，正 = 猫在右
                dx = (cat["cx"] - w / 2) / w

                if dx > TURN_THRESHOLD:
                    # 猫在右半边 → 顺时针旋转，让摄像头向右对准猫
                    self.api.rotate(clockwise=True)
                    action = "rotate_cw"
                elif dx < -TURN_THRESHOLD:
                    # 猫在左半边 → 逆时针旋转
                    self.api.rotate(clockwise=False)
                    action = "rotate_ccw"
                else:
                    # 猫已在正前方 → 前进
                    self.api.move("forward")
                    action = "forward"

                print(
                    f"[chaser] conf={cat['conf']:.2f}  "
                    f"box={box_ratio:.0%}  dx={dx:+.2f}  → {action}"
                )
                time.sleep(LOOP_INTERVAL)

        finally:
            self.api.stop()
            print("[chaser] stopped")
