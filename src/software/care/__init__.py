"""
照料模块 — WaterMonitor + ComfortResponder + CareLoop

WaterMonitor:
    30s 轮询 api.state()，读取超声波传感器 1（正前方，机器人停在水碗旁）：
        水面下降 → 传感器距离增大 >= DRINK_DELTA_MM → 记录饮水事件
        距离 >= LOW_WATER_MM                        → 水量不足告警
        连续 NO_DRINK_HOURS 小时未饮水               → 不饮水告警
    所有事件写入 SQLite data/hunter.db。

ComfortResponder:
    接收来自主循环的情绪信号（与 SoundClassifier 对接）：
        distress / hunger → 缓慢接近猫咪 + 播放安抚猫叫 + 后退
        content / purr    → 轻声回应猫叫
    内置 COMFORT_COOLDOWN 秒冷却，避免重复打扰。

CareLoop:
    把 WaterMonitor + ComfortResponder 打包成一个可组合的后台服务，
    对外暴露 start() / stop() / on_emotion()。

用法::

    api  = HunterAPI()
    care = CareLoop(
        api,
        on_no_drink  = lambda: print("推送：猫咪超12小时未饮水！"),
        on_low_water = lambda: print("推送：水碗快空了！"),
    )
    care.start()

    # 在主循环里：
    care.on_emotion("distress", confidence=0.91)
    care.on_emotion("purr",     confidence=0.85)

    # 查询今日饮水次数：
    print(care.water.drink_count_today())

    care.stop()
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Callable, Optional

from src.api_client import HunterAPI

# ── 数据库路径（与 memory / report 共享同一 DB）────────────────────────────
_DB_PATH = Path(__file__).parent.parent.parent / "data" / "hunter.db"


def _ensure_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB_PATH) as cx:
        cx.executescript("""
            CREATE TABLE IF NOT EXISTS drink_events (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        INTEGER NOT NULL,
                sensor_mm INTEGER NOT NULL,
                delta_mm  INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS water_alerts (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                ts   INTEGER NOT NULL,
                kind TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS comfort_events (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       INTEGER NOT NULL,
                emotion  TEXT NOT NULL,
                sound_n  INTEGER NOT NULL
            );
        """)


@contextmanager
def _db():
    with sqlite3.connect(_DB_PATH) as cx:
        cx.row_factory = sqlite3.Row
        yield cx


# ── 配置常量 ──────────────────────────────────────────────────────────────
WATER_SENSOR    = 1      # 正前超声波，机器人停在水碗前时对准水面
DRINK_DELTA_MM  = 10     # 相邻两次读数增量 >= 此值 → 饮水事件
LOW_WATER_MM    = 150    # 绝对距离 >= 此值 → 水量不足（水面离传感器过远）
NO_DRINK_HOURS  = 12     # 超过此小时未饮水 → 不饮水告警
POLL_INTERVAL   = 30     # 水位轮询间隔（秒）

COMFORT_COOLDOWN  = 120  # 两次安抚行为之间的最短冷却（秒）
APPROACH_DURATION = 0.6  # 接近猫咪的前进时间（秒）
HOLD_INTERVAL     = 0.10 # hold-to-move 脉冲间隔（秒），API 要求 ≤ 100ms


# ─────────────────────────────────────────────────────────────────────────
# WaterMonitor
# ─────────────────────────────────────────────────────────────────────────

class WaterMonitor:
    """
    超声波饮水监测器。

    原理：机器人停在水碗旁，传感器 1 正对水面。
        水面越低 → 距离读数越大。
        相邻两次读数差 >= DRINK_DELTA_MM → 水面下降 → 猫咪饮水了。

    告警：
        12h 未饮水 → on_no_drink()
        距离 >= LOW_WATER_MM → on_low_water()（水碗快空）
    """

    def __init__(
        self,
        api: HunterAPI,
        on_no_drink:  Optional[Callable[[], None]] = None,
        on_low_water: Optional[Callable[[], None]] = None,
    ) -> None:
        self.api          = api
        self.on_no_drink  = on_no_drink
        self.on_low_water = on_low_water

        self._stop            = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._prev_mm: Optional[int] = None
        self._last_drink_ts   = time.time()
        self._no_drink_alerted = False

        _ensure_db()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="WaterMonitor"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    # ── 内部 ──────────────────────────────────────────────────────────

    def _loop(self) -> None:
        # 建立初始基线
        self._prev_mm = self._read_sensor()
        print(f"[WaterMonitor] start  baseline={self._prev_mm} mm")
        while not self._stop.wait(POLL_INTERVAL):
            try:
                self._tick()
            except Exception as exc:
                print(f"[WaterMonitor] error: {exc}")

    def _tick(self) -> None:
        mm = self._read_sensor()
        if mm is None or self._prev_mm is None:
            return

        delta = mm - self._prev_mm  # 正值 = 水面下降 = 饮水

        if delta >= DRINK_DELTA_MM:
            self._record_drink(mm, delta)
            self._last_drink_ts   = time.time()
            self._no_drink_alerted = False
            print(f"[WaterMonitor] drink  Δ={delta}mm  now={mm}mm")

        # 12h 无饮水告警
        if (time.time() - self._last_drink_ts) / 3600 >= NO_DRINK_HOURS:
            if not self._no_drink_alerted:
                self._no_drink_alerted = True
                self._record_alert("no_drink")
                if self.on_no_drink:
                    threading.Thread(target=self.on_no_drink, daemon=True).start()

        # 水碗快空告警
        if mm >= LOW_WATER_MM:
            self._record_alert("low_water")
            if self.on_low_water:
                threading.Thread(target=self.on_low_water, daemon=True).start()

        self._prev_mm = mm

    def _read_sensor(self) -> Optional[int]:
        try:
            data  = self.api.state()
            entry = data.get("state", {}).get("ultra", {}).get(str(WATER_SENSOR))
            if entry and entry.get("distance_mm") is not None:
                return int(entry["distance_mm"])
        except Exception as exc:
            print(f"[WaterMonitor] api.state() error: {exc}")
        return None

    def _record_drink(self, mm: int, delta: int) -> None:
        ts = int(time.time() * 1000)
        with _db() as cx:
            cx.execute(
                "INSERT INTO drink_events (ts, sensor_mm, delta_mm) VALUES (?,?,?)",
                (ts, mm, delta),
            )

    def _record_alert(self, kind: str) -> None:
        ts = int(time.time() * 1000)
        with _db() as cx:
            cx.execute(
                "INSERT INTO water_alerts (ts, kind) VALUES (?,?)",
                (ts, kind),
            )
        print(f"[WaterMonitor] ALERT: {kind}")

    # ── 查询接口 ──────────────────────────────────────────────────────

    def drink_count_today(self) -> int:
        """返回今日已记录的饮水次数。"""
        midnight = int(
            datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp() * 1000
        )
        with _db() as cx:
            row = cx.execute(
                "SELECT COUNT(*) FROM drink_events WHERE ts >= ?", (midnight,)
            ).fetchone()
        return row[0] if row else 0

    def hours_since_last_drink(self) -> float:
        """返回距上次饮水的小时数。"""
        return (time.time() - self._last_drink_ts) / 3600


# ─────────────────────────────────────────────────────────────────────────
# ComfortResponder
# ─────────────────────────────────────────────────────────────────────────

class ComfortResponder:
    """
    情绪驱动安抚行为：

        distress / hunger:
            1. 缓慢接近猫咪（前进 APPROACH_DURATION 秒）
            2. 停车
            3. 播放安抚猫叫
            4. 等待 1.5s 让猫反应
            5. 缓慢后退（同时长）

        content / purr:
            仅播放轻柔回应猫叫，不移动。

    冷却：COMFORT_COOLDOWN 秒内不重复触发，避免打扰猫咪。
    """

    _SOUND: dict[str, int] = {
        "distress": 2,
        "hunger":   3,
        "content":  1,
        "purr":     1,
        "alert":    4,
    }

    def __init__(self, api: HunterAPI) -> None:
        self.api       = api
        self._last_ts  = 0.0
        self._lock     = threading.Lock()
        _ensure_db()

    def on_emotion(self, emotion: str, confidence: float = 1.0) -> None:
        """
        主循环调用此函数传入当前情绪。
        只有 distress / hunger / content / purr / alert 有响应。
        所有触发事件写入 comfort_events，供日报读取。
        """
        if emotion not in self._SOUND:
            return

        with self._lock:
            if time.time() - self._last_ts < COMFORT_COOLDOWN:
                return
            self._last_ts = time.time()

        threading.Thread(
            target=self._respond,
            args=(emotion, confidence),
            daemon=True,
            name="ComfortResponder",
        ).start()

    # ── 内部 ──────────────────────────────────────────────────────────

    def _respond(self, emotion: str, confidence: float) -> None:
        sound_n = self._SOUND[emotion]
        move    = emotion in ("distress", "hunger")

        try:
            if move:
                self._drive("forward", APPROACH_DURATION)

            self.api.play_cat_sound(sound_n)

            if move:
                time.sleep(1.5)
                self._drive("backward", APPROACH_DURATION)

        except Exception as exc:
            print(f"[ComfortResponder] error during respond: {exc}")
            try:
                self.api.stop()
            except Exception:
                pass

        self._log(emotion, sound_n)
        print(f"[ComfortResponder] emotion={emotion!r}  sound={sound_n}  move={move}")

    def _drive(self, direction: str, duration: float) -> None:
        end = time.time() + duration
        while time.time() < end:
            self.api.move(direction)
            time.sleep(HOLD_INTERVAL)
        self.api.stop()

    def _log(self, emotion: str, sound_n: int) -> None:
        ts = int(time.time() * 1000)
        with _db() as cx:
            cx.execute(
                "INSERT INTO comfort_events (ts, emotion, sound_n) VALUES (?,?,?)",
                (ts, emotion, sound_n),
            )


# ─────────────────────────────────────────────────────────────────────────
# CareLoop  —  对外统一接口
# ─────────────────────────────────────────────────────────────────────────

class CareLoop:
    """
    将 WaterMonitor 与 ComfortResponder 整合为单一照料服务。

    ::

        care = CareLoop(api, on_no_drink=push_alert, on_low_water=push_alert)
        care.start()

        # 主循环每帧调用：
        care.on_emotion(emotion_str, confidence)

        # 查询：
        care.water.drink_count_today()
        care.water.hours_since_last_drink()

        care.stop()
    """

    def __init__(
        self,
        api: HunterAPI,
        on_no_drink:  Optional[Callable[[], None]] = None,
        on_low_water: Optional[Callable[[], None]] = None,
    ) -> None:
        self.water    = WaterMonitor(api, on_no_drink, on_low_water)
        self.comfort  = ComfortResponder(api)

    def start(self) -> None:
        self.water.start()
        print("[CareLoop] started")

    def stop(self) -> None:
        self.water.stop()
        print("[CareLoop] stopped")

    def on_emotion(self, emotion: str, confidence: float = 1.0) -> None:
        """转发情绪信号给 ComfortResponder。主循环每帧调用。"""
        self.comfort.on_emotion(emotion, confidence)
