#!/usr/bin/env python3
"""
ROBOT CONTROL UNIT // RPI-01
Web console for the 3WD yahboom car — wraps car_driver + audio_driver + USB cam.

Run:
    python3 ~/Desktop/web_console.py
    # listens on 0.0.0.0:8000
"""

import asyncio
import json
import os
import sys
import time
import socket
import threading
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser("~/Desktop"))

# 用 default pyserial behaviour (跟 main_test.py 一致)
import serial as _pyserial

from car_driver import CarController
from audio_driver import AudioController

import cv2
import numpy as np

# YOLO 是可选依赖,服务在 venv (有 ultralytics) 或系统 python (没有) 都能跑
try:
    from ultralytics import YOLO
    _yolo_ok = True
except Exception:
    _yolo_ok = False
    YOLO = None

# 机械臂(可选):https://github.com/zhanghy12/Minimal_interface_moce
# 假设 clone 到 /home/pi/Minimal_interface_moce,pip install -e 后 physical_agent 可 import
ARM_REPO_PATH = "/home/pi/Minimal_interface_moce"
ARM_CONFIG_PATH = os.path.expanduser("~/Desktop/arm_config.json")
try:
    if os.path.isdir(os.path.join(ARM_REPO_PATH, "src")):
        sys.path.insert(0, os.path.join(ARM_REPO_PATH, "src"))
    from physical_agent.controller import Sts3215ArmController
    _arm_ok = True
except Exception:
    _arm_ok = False
    Sts3215ArmController = None

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse


REC_DIR = "/home/pi/car_project/records"
SOUND_DIR = "/home/pi/car_project/sounds"
UPLOADS_DIR = "/home/pi/car_project/uploads"
ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"}
MAX_UPLOAD_SIZE = 30 * 1024 * 1024  # 30 MB,够一首歌

car: CarController | None = None
audio: AudioController | None = None
camera: "CameraStreamer | None" = None
yolo: "YOLOInferencer | None" = None
minimap: "MiniMap | None" = None
holder: "HoldController" = None  # type: ignore
arm: "Sts3215ArmController | None" = None
arm_lock = threading.Lock()
loop: asyncio.AbstractEventLoop | None = None

YOLO_MODELS_DIR = "/home/pi/yolo_env"
DEFAULT_YOLO_MODEL = "/home/pi/yolo_env/yolo26n.pt"  # 2026-05 升级:RPi 5 上 latency 383→133ms (2.9×),FPS 3→6.9
clients: set[WebSocket] = set()
log_buf: deque = deque(maxlen=300)

state: dict = {
    "ultra": {1: None, 2: None, 3: None},
    "modes": {
        "obstacle": False,
        "ultra_report": False,
        "emergency": False,
    },
    "speed": None,
    "last_action": "STOP",
    "recording": False,
    "rec_path": None,
    "rec_start_t": None,
    "esp32_deadlock": False,
}

MAX_RECORDING_DURATION_S = 5 * 60  # 5 分钟自动 stop,防止前端断开导致孤儿录音

t_boot = time.time()
t_last_esp32 = 0.0  # last time any ESP32 line was received


def now_ms() -> int:
    return int(time.time() * 1000)


# ====================== Camera ======================

class CameraStreamer:
    """USB UVC camera -> MJPEG stream via shared latest_frame buffer."""

    def __init__(self, device=0, width=640, height=480, fps=20, quality=72):
        self.device = device
        self.width = width
        self.height = height
        self.target_fps = fps
        self.quality = quality
        self.cap = None
        self.latest_jpeg = None
        self.last_frame_t = 0.0
        self.frame_count = 0
        self.running = False
        self.thread = None
        self.error: str | None = None
        self.lock = threading.Lock()
        self._fps_window = deque(maxlen=30)
        self.flipped = False  # 正反颠倒(H+V 同时翻 = 180° 旋转)

    def start(self) -> bool:
        try:
            self.cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
            if not self.cap.isOpened():
                self.cap = cv2.VideoCapture(self.device)
            if not self.cap.isOpened():
                self.error = "cv2.VideoCapture failed"
                self.cap = None
                return False
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.running = True
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
            return True
        except Exception as e:
            self.error = f"start: {e}"
            return False

    def _loop(self):
        delay = 1.0 / max(1.0, self.target_fps)
        fail_streak = 0
        FAIL_THRESHOLD = 15        # 连续这么多帧失败就触发重连
        RECONNECT_BACKOFF = 2.0    # 重连失败后等多久再试
        WATCHDOG_S = 5.0           # 超过这么多秒无新帧 → 强制 reopen(USB 断开后 read() 可能 hang 不增 streak)
        self.last_frame_t = time.time()  # 启动时 seed 避免 watchdog 立即触发
        while self.running:
            t0 = time.time()
            # Time-based watchdog:read 可能 hang 不返回,streak 不增长 → 用时间判定
            if (time.time() - self.last_frame_t) > WATCHDOG_S:
                self.error = f"watchdog: no frame {time.time()-self.last_frame_t:.1f}s, reopening"
                self._reopen()
                self.last_frame_t = time.time()  # reset 防止反复触发
                fail_streak = 0
                time.sleep(RECONNECT_BACKOFF)
                continue
            try:
                ok, frame = (self.cap.read() if self.cap is not None else (False, None))
            except Exception as e:
                self.error = f"read: {e}"
                ok, frame = False, None
            if not ok or frame is None:
                fail_streak += 1
                self.error = f"read failed (streak {fail_streak})"
                if fail_streak >= FAIL_THRESHOLD:
                    self._reopen()
                    fail_streak = 0
                    time.sleep(RECONNECT_BACKOFF)
                else:
                    time.sleep(0.2)
                continue
            fail_streak = 0
            self.error = None
            # 正反颠倒(180° 旋转 = H + V 翻转 = cv2.flip(_, -1))
            if self.flipped:
                frame = cv2.flip(frame, -1)
            try:
                ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
            except Exception as e:
                self.error = f"encode: {e}"
                time.sleep(0.2)
                continue
            if ok2:
                with self.lock:
                    self.latest_jpeg = bytes(buf)
                    self.last_frame_t = time.time()
                    self.frame_count += 1
                    self._fps_window.append(self.last_frame_t)
            dt = time.time() - t0
            if dt < delay:
                time.sleep(delay - dt)

    def _reopen(self):
        """Release current handle and try to re-open the V4L2 device (e.g. after USB hot-unplug)."""
        try:
            if self.cap is not None:
                self.cap.release()
        except Exception:
            pass
        self.cap = None
        try:
            cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
            if not cap.isOpened():
                cap = cv2.VideoCapture(self.device)
            if not cap.isOpened():
                self.error = "reopen: VideoCapture still closed"
                return False
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.target_fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.cap = cap
            self.error = None
            return True
        except Exception as e:
            self.error = f"reopen: {e}"
            return False

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
        if self.cap:
            try: self.cap.release()
            except Exception: pass
        self.cap = None

    def get_jpeg(self):
        with self.lock:
            return self.latest_jpeg, self.last_frame_t

    def is_alive(self) -> bool:
        if self.cap is None or not self.cap.isOpened():
            return False
        return (time.time() - self.last_frame_t) < 3.0

    def fps(self) -> float:
        # 如果 loop 已死(last_frame_t 老 >3s),fps 报 0 而不是用 stale deque
        if self.last_frame_t and (time.time() - self.last_frame_t) > 3.0:
            return 0.0
        with self.lock:
            w = list(self._fps_window)
        if len(w) < 2:
            return 0.0
        span = w[-1] - w[0]
        return (len(w) - 1) / span if span > 0 else 0.0


# ====================== MiniMap / dead-reckoning + occupancy ======================
import math as _math

class MiniMap:
    """Pure software dead-reckoning + occupancy grid. Memory only, reset on service restart.

    输入:
      - cmd(action) 通过 install_event_hooks 自动触发 → 维持 self.current_action
      - BUTTONSPEED 回应 → 更新速度估计
      - ULTRA 数据 → 投影到世界坐标,累积到 occupancy grid

    位置坐标系 (right-handed,+x 起始朝向,+y 起始左侧,theta 逆时针):
      - 服务启动那一刻 = 原点 + theta=0
      - 服务重启清空所有状态
    """

    # 速度模型常数(初始猜测,可通过 /api/map/config 实时标定)
    K_LINEAR = 0.05    # mm/s per BUTTONSPEED. BS=2000 → 100mm/s
    K_ANGULAR = 0.0006 # rad/s per BUTTONSPEED. BS=2000 → 1.2 rad/s ≈ 69°/s
    # 重心前置 → 后驱效率低于前驱,后退速度 / 前进速度 比率
    BACKWARD_RATIO = 0.7
    # 传感器朝向(车体局部,弧度,基于 schematic S2 左前 / S1 前 / S3 右前)
    SENSOR_YAW = {1: 0.0, 2: _math.radians(50), 3: _math.radians(-50)}
    SENSOR_OFFSET_X_MM = 50
    GRID_CELL_MM = 100        # 10cm
    MAX_HIT_RANGE_MM = 3500   # 超过此距离的 ULTRA 当噪声

    def __init__(self):
        self.x_mm = 0.0
        self.y_mm = 0.0
        self.theta = 0.0
        self.current_action: str | None = None
        self.button_speed = 2000
        self.last_step_t = time.time()
        self.lock = threading.Lock()
        self.grid: dict[tuple[int, int], int] = {}
        self.recent_hits: deque = deque(maxlen=80)
        self.pose_history: deque = deque(maxlen=300)
        self.running = False
        self.thread = None

    def set_action(self, action: str | None):
        with self.lock:
            self.current_action = action

    def set_button_speed(self, bs: int):
        with self.lock:
            try: self.button_speed = max(500, int(bs))
            except Exception: pass

    def reset(self):
        with self.lock:
            self.x_mm = 0.0; self.y_mm = 0.0; self.theta = 0.0
            self.grid.clear()
            self.recent_hits.clear()
            self.pose_history.clear()

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        DT = 0.05  # 50ms step
        counter = 0
        while self.running:
            time.sleep(DT)
            with self.lock:
                self._step(DT)
            counter += 1
            # 每 ~500ms 采一次轨迹点(仅当位置变化时)
            if counter % 10 == 0:
                with self.lock:
                    last = self.pose_history[-1] if self.pose_history else None
                    if last is None or _math.hypot(self.x_mm - last["x"], self.y_mm - last["y"]) > 20:
                        self.pose_history.append({
                            "x": round(self.x_mm, 1),
                            "y": round(self.y_mm, 1),
                            "t": int(time.time() * 1000),
                        })

    def _step(self, dt: float):
        a = self.current_action
        # 只有方向类 cmd 持续作用;其他都是脉冲(stop/cat/speed)
        if a not in ("FORWARD", "BACKWARD", "LEFT", "RIGHT", "ROT_CW", "ROT_CCW"):
            return
        v = self.K_LINEAR * self.button_speed
        w = self.K_ANGULAR * self.button_speed
        vx_local = vy_local = dw = 0.0
        if a == "FORWARD":    vx_local = v
        elif a == "BACKWARD": vx_local = -v * self.BACKWARD_RATIO
        elif a == "LEFT":     vy_local = v
        elif a == "RIGHT":    vy_local = -v
        elif a == "ROT_CCW":  dw = w
        elif a == "ROT_CW":   dw = -w
        c, s = _math.cos(self.theta), _math.sin(self.theta)
        self.x_mm += (vx_local * c - vy_local * s) * dt
        self.y_mm += (vx_local * s + vy_local * c) * dt
        self.theta += dw * dt
        # normalize to [-pi, pi]
        while self.theta > _math.pi:  self.theta -= 2 * _math.pi
        while self.theta < -_math.pi: self.theta += 2 * _math.pi

    def on_ultra(self, sensor_id: int, distance_mm: int, has_obstacle: bool):
        if not has_obstacle: return
        if distance_mm < 30 or distance_mm > self.MAX_HIT_RANGE_MM: return
        with self.lock:
            sensor_yaw_local = self.SENSOR_YAW.get(sensor_id, 0.0)
            # 传感器世界朝向
            beam_theta = self.theta + sensor_yaw_local
            # 传感器世界位置(车前方 50mm 处)
            sx = self.x_mm + self.SENSOR_OFFSET_X_MM * _math.cos(self.theta)
            sy = self.y_mm + self.SENSOR_OFFSET_X_MM * _math.sin(self.theta)
            # 障碍点 = sensor + distance 沿 beam 方向
            hx = sx + distance_mm * _math.cos(beam_theta)
            hy = sy + distance_mm * _math.sin(beam_theta)
            cx = int(hx // self.GRID_CELL_MM)
            cy = int(hy // self.GRID_CELL_MM)
            self.grid[(cx, cy)] = self.grid.get((cx, cy), 0) + 1
            self.recent_hits.append({"t": int(time.time() * 1000),
                                     "x": round(hx, 1), "y": round(hy, 1),
                                     "sensor": sensor_id})

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "pose": {
                    "x": round(self.x_mm, 1),
                    "y": round(self.y_mm, 1),
                    "theta": round(self.theta, 4),
                    "theta_deg": round(_math.degrees(self.theta), 1),
                },
                "current_action": self.current_action,
                "button_speed": self.button_speed,
                "cell_mm": self.GRID_CELL_MM,
                "cells": [
                    [cx * self.GRID_CELL_MM, cy * self.GRID_CELL_MM, n]
                    for (cx, cy), n in self.grid.items()
                ],
                "cell_count": len(self.grid),
                "recent_hits": list(self.recent_hits),
                "pose_history": list(self.pose_history),
                "config": {"k_linear": self.K_LINEAR, "k_angular": self.K_ANGULAR, "backward_ratio": self.BACKWARD_RATIO},
            }

    def configure(self, *, k_linear=None, k_angular=None, backward_ratio=None):
        with self.lock:
            if k_linear is not None:
                self.K_LINEAR = max(0.001, min(1.0, float(k_linear)))
            if k_angular is not None:
                self.K_ANGULAR = max(0.00001, min(0.01, float(k_angular)))
            if backward_ratio is not None:
                self.BACKWARD_RATIO = max(0.1, min(1.5, float(backward_ratio)))
        return self.snapshot()


# ====================== HoldController (移动持续控制) ======================
# 把"按住前进"逻辑从前端 setInterval 移到后端 thread,避免网络抖动让 cmd 间隔不稳。
# 前端通过 WS 发 down/up 信号,后端持续每 100ms 给 ESP32 发指令。
# 安全:1.5s 没 renew/up 自动 stop;WS 断开释放;急停时强制 release。

class HoldController:
    REPEAT_MS = 100
    TIMEOUT_S = 1.5

    def __init__(self):
        # action → last_renew_t,支持多键同时 hold;_loop 在它们之间 round-robin 发 cmd
        # (yahboom 协议是单字符互斥指令,无法原生组合,软件 RR 近似 W+Q 这种"边走边转"语义)
        self.actions: dict[str, float] = {}
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self._rr_idx = 0

    def hold(self, action: str):
        with self.lock:
            self.actions[action] = time.time()
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, daemon=True)
                self.thread.start()

    def renew(self, action: str | None = None):
        with self.lock:
            now = time.time()
            if action is None:
                for k in self.actions: self.actions[k] = now
            elif action in self.actions:
                self.actions[action] = now

    def release(self, action: str | None = None, reason: str = "up"):
        # action 给定 → 单独移除该键(剩余 action 继续 RR);None → 全清 + 停车
        with self.lock:
            if action is None:
                had_any = bool(self.actions)
                self.actions.clear()
            else:
                had_any = self.actions.pop(action, None) is not None
            empty = not self.actions
        if had_any and empty:
            try:
                if car is not None: car.stop()
            except Exception: pass
            push_log(f"hold release ({reason})", "cmd")

    def _loop(self):
        action_fns = {
            "forward":    "forward",
            "backward":   "backward",
            "left":       "left",
            "right":      "right",
            "rotate_cw":  "rotate_cw",
            "rotate_ccw": "rotate_ccw",
        }
        interval = self.REPEAT_MS / 1000.0
        next_t = time.monotonic()
        while True:
            with self.lock:
                now = time.time()
                # 清 timeout 的 action
                for k in list(self.actions.keys()):
                    if now - self.actions[k] > self.TIMEOUT_S:
                        del self.actions[k]
                keys = list(self.actions.keys())
            if not keys:
                # 全部松开/超时,停车后退出 loop
                try:
                    if car is not None: car.stop()
                except Exception: pass
                break
            # round-robin:多键时每 100ms 切换发不同 cmd,单键时只发它自己
            action = keys[self._rr_idx % len(keys)]
            self._rr_idx += 1
            try:
                if car is not None:
                    method_name = action_fns.get(action)
                    if method_name:
                        getattr(car, method_name)()
            except Exception as e:
                push_log(f"hold loop err: {e}", "sys")
            next_t += interval
            sleep_dur = next_t - time.monotonic()
            if sleep_dur > 0:
                time.sleep(sleep_dur)
            else:
                next_t = time.monotonic()


# ====================== Recording / Playback ======================
# 内存录制用户操作(cmd / hold 边沿)+ 传感器快照(ultra @ 10Hz, pose);支持正/倒放,
# 进度/当前指令显示,以及"校准 visualization":回放时把录制时的 ultra/pose 跟实时数据
# 推送给前端对比展示(自动微调 hold 时长留 Phase 2)。memory-only,服务重启清空。

class RecordingController:
    MAX_DURATION_S = 600   # 10 min 上限
    SENSOR_HZ = 10
    # cmd 倒放反映射(自反的写自己,音效不可逆但仍播放原音)
    INVERSE = {
        "forward": "backward", "backward": "forward",
        "left":    "right",    "right":    "left",
        "rotate_cw": "rotate_ccw", "rotate_ccw": "rotate_cw",
        "speed_up": "speed_down", "speed_down": "speed_up",
        "speed_reset": "speed_reset",
        "obstacle_on": "obstacle_off", "obstacle_off": "obstacle_on",
        "ultra_on": "ultra_off", "ultra_off": "ultra_on",
        "emergency": "emergency", "stop": "stop",
        "cat1": "cat1", "cat2": "cat2", "cat3": "cat3", "cat4": "cat4",
    }

    def __init__(self):
        self.events: list[tuple[float, str, dict]] = []
        self.t0 = 0.0
        self.recording = False
        self.playing = False
        self.playback = {
            "idx": 0, "total": 0, "elapsed": 0.0, "duration": 0.0,
            "direction": "forward", "speed": 1.0,
            "current": None, "calibrate": False,
            "trail_recorded": [], "trail_replay": [],
            "ultra_recorded": None, "ultra_live": None, "ultra_diff_mm": None,
        }
        self.lock = threading.Lock()
        self.sensor_thread = None
        self.playback_thread = None

    def status(self):
        with self.lock:
            duration = self.events[-1][0] if self.events else 0.0
            cmd_count = sum(1 for _, k, _ in self.events if k != "sensor")
            return {
                "recording": self.recording,
                "playing": self.playing,
                "event_count": cmd_count,
                "total_events": len(self.events),
                "duration_s": round(duration, 2),
                "elapsed_s": round(time.monotonic() - self.t0, 2) if self.recording else 0.0,
                "playback": dict(self.playback) if self.playing else None,
            }

    def start(self):
        with self.lock:
            if self.recording or self.playing: return (False, "already busy")
            self.events.clear()
            self.t0 = time.monotonic()
            self.recording = True
            self.sensor_thread = threading.Thread(target=self._sensor_loop, daemon=True)
            self.sensor_thread.start()
        push_log("recording started", "rec")
        return (True, None)

    def stop_recording(self):
        with self.lock:
            if not self.recording: return (False, "not recording")
            self.recording = False
            n = len(self.events)
            d = self.events[-1][0] if self.events else 0.0
        push_log(f"recording stopped: {n} events, {d:.1f}s", "rec")
        return (True, None)

    def clear(self):
        with self.lock:
            if self.recording or self.playing: return (False, "busy")
            self.events.clear()
        return (True, None)

    def log_event(self, kind: str, payload: dict):
        with self.lock:
            if not self.recording: return
            t = time.monotonic() - self.t0
            if t > self.MAX_DURATION_S:
                self.recording = False
                push_log("recording auto-stopped: max duration", "rec")
                return
            self.events.append((t, kind, payload))

    def _sensor_loop(self):
        interval = 1.0 / self.SENSOR_HZ
        next_t = time.monotonic()
        while True:
            with self.lock:
                if not self.recording: break
                t0 = self.t0
            t = time.monotonic() - t0
            ultra_raw = state.get("ultra") or {}
            ultra_dist = {}
            for k, v in ultra_raw.items():
                if isinstance(v, dict): ultra_dist[str(k)] = v.get("distance_mm")
            pose = None
            if minimap is not None:
                with minimap.lock:
                    pose = {"x": round(minimap.x_mm, 1), "y": round(minimap.y_mm, 1),
                            "theta": round(minimap.theta, 4)}
            snap = {"ultra": ultra_dist, "pose": pose, "speed": state.get("speed")}
            with self.lock:
                if self.recording: self.events.append((t, "sensor", snap))
            next_t += interval
            sleep_dur = next_t - time.monotonic()
            if sleep_dur > 0: time.sleep(sleep_dur)
            else: next_t = time.monotonic()

    def play(self, direction: str = "forward", speed: float = 1.0, calibrate: bool = False):
        with self.lock:
            if self.recording or self.playing: return (False, "busy")
            if not self.events: return (False, "no recording")
            events_snap = list(self.events)
            duration = events_snap[-1][0]
            trail_rec = [e[2]["pose"] for e in events_snap if e[1] == "sensor" and e[2].get("pose")]
            self.playing = True
            self.playback = {
                "idx": 0, "total": sum(1 for _, k, _ in events_snap if k != "sensor"),
                "elapsed": 0.0, "duration": duration,
                "direction": direction, "speed": max(0.1, min(5.0, float(speed))),
                "current": None, "calibrate": bool(calibrate),
                "trail_recorded": trail_rec, "trail_replay": [],
                "ultra_recorded": None, "ultra_live": None, "ultra_diff_mm": None,
            }
            self.playback_thread = threading.Thread(target=self._playback_loop, args=(events_snap,), daemon=True)
            self.playback_thread.start()
        push_log(f"playback started: {direction} x{speed}{' +cal' if calibrate else ''}", "rec")
        return (True, None)

    def stop_playback(self):
        with self.lock:
            if not self.playing: return (False, "not playing")
            self.playing = False
        try:
            if holder is not None: holder.release()
            if car is not None: car.stop()
        except Exception: pass
        push_log("playback stopped", "rec")
        return (True, None)

    def _prepare(self, events, direction):
        # 过滤掉 sensor,只保留 cmd 类
        cmd_only = [(t, k, p) for t, k, p in events if k != "sensor"]
        if direction != "reverse":
            return cmd_only
        duration = events[-1][0] if events else 0
        # 步骤 1:整体逆序 + cmd 反映射 + hold_down <-> hold_up 互换
        flipped = []
        for t, k, p in reversed(cmd_only):
            t_r = duration - t
            action = p.get("action") if isinstance(p, dict) else None
            inv = self.INVERSE.get(action, action) if action else None
            new_p = {"action": inv} if action else (p or {})
            if k == "hold_down": flipped.append([t_r, "hold_up", new_p])
            elif k == "hold_up": flipped.append([t_r, "hold_down", new_p])
            else: flipped.append([t_r, k, new_p])
        # 步骤 2:事件级 timing 补偿 — 反映射后 backward 段比原 forward 走得近(差 1/ratio),
        # forward 段比原 backward 走得远(差 ratio)。逐 hold 段调时长,后续事件 shift。
        ratio = (minimap.BACKWARD_RATIO if minimap is not None else 0.7)
        ratio = max(0.05, ratio)
        shift = 0.0
        hold_starts = {}
        for ev in flipped:
            ev[0] = ev[0] + shift
            t_adj, k, p = ev[0], ev[1], ev[2]
            action = p.get("action") if isinstance(p, dict) else None
            if k == "hold_down" and action:
                hold_starts[action] = t_adj
            elif k == "hold_up" and action and action in hold_starts:
                t_down = hold_starts.pop(action)
                orig = t_adj - t_down
                if action == "backward":
                    extra = orig * (1.0 / ratio - 1.0)
                elif action == "forward":
                    extra = orig * (ratio - 1.0)
                else:
                    extra = 0.0
                shift += extra
                ev[0] = t_adj + extra
        return [tuple(e) for e in flipped]

    def _playback_loop(self, events_snap):
        with self.lock:
            direction = self.playback["direction"]
            speed = self.playback["speed"]
            calibrate = self.playback["calibrate"]
        prepared = self._prepare(events_snap, direction)
        sensor_log = [(t, p) for t, k, p in events_snap if k == "sensor"]

        def find_sensor(t_orig):
            # 二分查找 sensor 时间最近邻
            if not sensor_log: return None
            best = sensor_log[0]; best_d = abs(sensor_log[0][0] - t_orig)
            for s in sensor_log:
                d = abs(s[0] - t_orig)
                if d < best_d: best = s; best_d = d
            return best[1]

        start = time.monotonic()
        i = 0
        try:
            while i < len(prepared):
                with self.lock:
                    if not self.playing: break
                t_ev, kind, payload = prepared[i]
                target = start + t_ev / speed
                now = time.monotonic()
                if target > now: time.sleep(target - now)
                with self.lock:
                    if not self.playing: break
                self._dispatch(kind, payload)
                # 校准 visualization:取原录时 t_ev 对应 sensor,跟实时 sensor 对比
                cal_data = None
                if calibrate:
                    orig_t = events_snap[-1][0] - t_ev if direction == "reverse" else t_ev
                    rec_snap = find_sensor(orig_t)
                    if rec_snap:
                        live_ultra = {}
                        for k, v in (state.get("ultra") or {}).items():
                            if isinstance(v, dict): live_ultra[str(k)] = v.get("distance_mm")
                        rec_ultra = rec_snap.get("ultra") or {}
                        diff = {}
                        for sk in set(list(rec_ultra.keys()) + list(live_ultra.keys())):
                            r = rec_ultra.get(sk); l = live_ultra.get(sk)
                            if r is not None and l is not None:
                                diff[sk] = l - r
                        cal_data = {"recorded": rec_ultra, "live": live_ultra, "diff": diff}
                with self.lock:
                    self.playback["idx"] = i + 1
                    self.playback["elapsed"] = t_ev
                    self.playback["current"] = {"kind": kind, "action": payload.get("action") if isinstance(payload, dict) else None}
                    if minimap is not None:
                        with minimap.lock:
                            self.playback["trail_replay"].append({"x": round(minimap.x_mm, 1), "y": round(minimap.y_mm, 1)})
                    if cal_data:
                        self.playback["ultra_recorded"] = cal_data["recorded"]
                        self.playback["ultra_live"] = cal_data["live"]
                        self.playback["ultra_diff_mm"] = cal_data["diff"]
                i += 1
        finally:
            try:
                if holder is not None: holder.release()
                if car is not None: car.stop()
            except Exception: pass
            with self.lock:
                self.playing = False
                self.playback["current"] = None
            push_log("playback finished", "rec")

    def _dispatch(self, kind, payload):
        action = payload.get("action") if isinstance(payload, dict) else None
        if not action: return
        try:
            if kind == "hold_down":
                if holder: holder.hold(action)
            elif kind == "hold_up":
                if holder: holder.release(action)
            elif kind == "hold_renew":
                if holder: holder.renew(action)
            elif kind == "cmd":
                fn = CMD_MAP.get(action)
                if fn: fn()
        except Exception as e:
            push_log(f"playback dispatch err {kind}/{action}: {e}", "rec")


recorder: "RecordingController" = None  # type: ignore


# ====================== YOLO inference ======================

class YOLOInferencer:
    """
    后台 YOLO 推理:从 camera 拿最新 JPEG → decode → ultralytics 推理。
    enabled=False 时空闲不耗 CPU。
    检测结果归一化坐标 [0,1] 存到 latest_detections,前端 SVG 按视频区域比例画 bbox。
    每次 configure() 后会持久化状态到 STATE_FILE,服务重启自动恢复。
    """

    STATE_FILE = "/home/pi/.config/robot-console/yolo_state.json"

    def __init__(self, model_path: str, get_jpeg_fn, on_detection=None):
        self.model_path = model_path
        self.get_jpeg_fn = get_jpeg_fn
        self.on_detection = on_detection
        self.model = None
        self.model_loaded = False
        self.load_error: str | None = None

        # 可调配置
        self.enabled = False
        self.conf = 0.40
        self.iou = 0.50
        self.imgsz = 416
        self.classes: list[int] | None = None
        self.min_interval = 0.5  # 推理之间最小间隔(s)

        # 运行态
        self.running = False
        self.thread = None
        self.latest_detections: list[dict] = []
        self.latest_inference_t = 0.0
        self.latest_inference_ms = 0
        self.frame_shape: tuple[int, int] | None = None
        self.lock = threading.Lock()
        self.available_classes: dict[int, str] = {}
        self.error: str | None = None

    def load_model(self):
        if not _yolo_ok:
            self.load_error = "ultralytics not installed"
            return False
        try:
            self.model = YOLO(self.model_path)
            self.available_classes = dict(self.model.names)
            self.model_loaded = True
            self.load_error = None
            return True
        except Exception as e:
            self.load_error = str(e)
            return False

    def start(self) -> bool:
        if not _yolo_ok:
            return False
        if not self.model_loaded and not self.load_model():
            return False
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            if not self.enabled or self.get_jpeg_fn is None:
                time.sleep(0.25)
                continue
            jpeg, _ = self.get_jpeg_fn()
            if jpeg is None:
                time.sleep(0.1)
                continue
            try:
                arr = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if arr is None:
                    time.sleep(0.1); continue
                t0 = time.time()
                results = self.model(
                    arr, conf=self.conf, iou=self.iou, imgsz=self.imgsz,
                    classes=self.classes, verbose=False,
                )
                dt = (time.time() - t0) * 1000
                h, w = arr.shape[:2]
                dets = []
                if results:
                    r = results[0]
                    for box in r.boxes:
                        x1, y1, x2, y2 = box.xyxy[0].tolist()
                        cls_i = int(box.cls[0])
                        dets.append({
                            "x": round(x1 / w, 4), "y": round(y1 / h, 4),
                            "w": round((x2 - x1) / w, 4),
                            "h": round((y2 - y1) / h, 4),
                            "conf": round(float(box.conf[0]), 3),
                            "cls": cls_i,
                            "label": self.model.names.get(cls_i, str(cls_i)),
                        })
                with self.lock:
                    self.latest_detections = dets
                    self.latest_inference_t = time.time()
                    self.latest_inference_ms = int(dt)
                    self.frame_shape = (h, w)
                    self.error = None
                if self.on_detection:
                    self.on_detection({
                        "detections": dets,
                        "inference_ms": int(dt),
                        "t": int(self.latest_inference_t * 1000),
                    })
                # 节流:即使 inference 很快,也保证 min_interval
                elapsed = (time.time() - t0)
                if elapsed < self.min_interval:
                    time.sleep(self.min_interval - elapsed)
            except Exception as e:
                self.error = str(e)
                time.sleep(0.5)

    def get_state(self) -> dict:
        with self.lock:
            return {
                "available": _yolo_ok,
                "loaded": self.model_loaded,
                "enabled": self.enabled,
                "model": self.model_path,
                "model_classes": [
                    {"id": k, "name": v} for k, v in sorted(self.available_classes.items())
                ],
                "config": {
                    "conf": self.conf, "iou": self.iou, "imgsz": self.imgsz,
                    "classes": list(self.classes) if self.classes is not None else None,
                    "min_interval": self.min_interval,
                },
                "latest": {
                    "detections": list(self.latest_detections),
                    "inference_ms": self.latest_inference_ms,
                    "frame_shape": self.frame_shape,
                    "t_ms": int(self.latest_inference_t * 1000) if self.latest_inference_t else None,
                },
                "load_error": self.load_error,
                "error": self.error,
            }

    _UNSET = object()

    def configure(self, *, enabled=_UNSET, conf=_UNSET, iou=_UNSET, imgsz=_UNSET,
                  classes=_UNSET, min_interval=_UNSET) -> dict:
        if enabled is not self._UNSET:
            self.enabled = bool(enabled)
            if not self.enabled:
                with self.lock:
                    self.latest_detections = []
        if conf is not self._UNSET and conf is not None:
            self.conf = max(0.01, min(0.99, float(conf)))
        if iou is not self._UNSET and iou is not None:
            self.iou = max(0.0, min(1.0, float(iou)))
        if imgsz is not self._UNSET and imgsz is not None:
            v = int(imgsz)
            v = max(160, min(1280, (v // 32) * 32))
            self.imgsz = v
        if classes is not self._UNSET:
            # null / [] / "all" / False 都表示"全部类别(不过滤)"
            if classes is None or classes in (False, "all", []):
                self.classes = None
            else:
                self.classes = [int(c) for c in classes]
        if min_interval is not self._UNSET and min_interval is not None:
            self.min_interval = max(0.05, min(10.0, float(min_interval)))
        self._save_state()
        return self.get_state()

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
            with open(self.STATE_FILE, "w") as f:
                json.dump({
                    "enabled": self.enabled,
                    "conf": self.conf,
                    "iou": self.iou,
                    "imgsz": self.imgsz,
                    "min_interval": self.min_interval,
                    "classes": list(self.classes) if self.classes is not None else None,
                }, f, indent=2)
        except Exception:
            pass

    def load_state(self) -> bool:
        """Service 启动后调用,从持久化文件恢复 enabled/conf/imgsz 等。"""
        try:
            with open(self.STATE_FILE) as f:
                st = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        except Exception:
            return False
        if "enabled" in st: self.enabled = bool(st["enabled"])
        if "conf" in st: self.conf = float(st["conf"])
        if "iou" in st: self.iou = float(st["iou"])
        if "imgsz" in st:
            v = int(st["imgsz"])
            self.imgsz = max(160, min(1280, (v // 32) * 32))
        if "min_interval" in st: self.min_interval = float(st["min_interval"])
        if "classes" in st:
            self.classes = None if st["classes"] is None else [int(c) for c in st["classes"]]
        return True


# ====================== Pub/sub ======================

async def _broadcast(payload: dict):
    text = json.dumps(payload, ensure_ascii=False, default=str)
    dead = []
    for ws in list(clients):
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for d in dead:
        clients.discard(d)


def bcast(payload: dict):
    if loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(_broadcast(payload), loop)
    except Exception:
        pass


def push_log(line: str, kind: str = "esp32"):
    entry = {"t": now_ms(), "kind": kind, "line": line}
    log_buf.append(entry)
    bcast({"type": "log", "data": entry})


SOFT_AVOID_THRESHOLD_MM = 250  # 软件避障触发距离

def on_ultra(data: dict):
    sid = data["sensor_id"]
    state["ultra"][sid] = {
        "distance_mm": data["distance_mm"],
        "has_obstacle": data["has_obstacle"],
        "t": now_ms(),
    }
    bcast({"type": "ultra", "data": state["ultra"]})
    if minimap is not None:
        minimap.on_ultra(sid, data["distance_mm"], data["has_obstacle"])
    # 软件避障:OBSTACLE 模式启用且任一 sensor 报有障碍且距离 < 阈值 → 主动 stop
    # (用 service 替代 ESP32 自主避障,这样 ULTRA_REPORT 和"避障"可以同时开,minimap 也能记轨迹)
    if state["modes"].get("obstacle") and data["has_obstacle"] and data["distance_mm"] < SOFT_AVOID_THRESHOLD_MM:
        if state["last_action"] not in ("STOP", "EMERGENCY_STOP"):
            push_log(f"soft-avoid: S{sid} {data['distance_mm']}mm obstacle → STOP", "cmd")
            try:
                if car is not None: car.stop()
            except Exception: pass


def on_ps3_l1():
    push_log("PS3_L1_DOWN -> arm_action_1", "ps3")


def on_ps3_l2():
    push_log("PS3_L2_DOWN -> arm_action_2", "ps3")


# 音频播放前的电流稳定 helper(防止 USB Audio 启动瞬时电流 + 电机电流叠加触发 over-current)
# 触发场景已知:用户按猫叫/上传播放 → USB DAC 启动有 200-400mA 冲 → 跟运行中电机叠加 → 4 个 USB root port 同时 over-current 保护 → 整个 USB 崩 + ESP32 firmware deadlock
def _audio_preflight():
    """音频播放前的电流预防:如果车正在动,先停车 + 等总电流稳定。"""
    if car is None: return
    if state["last_action"] in ("FORWARD","BACKWARD","LEFT","RIGHT","ROT_CW","ROT_CCW"):
        try:
            car.stop()
            push_log("audio preflight: stopped motor to avoid USB over-current", "audio")
            time.sleep(0.25)  # 让电流稳下来
        except Exception: pass


def install_event_hooks():
    orig_handle = car._handle_esp32_line

    def wrapped_handle(line: str):
        global t_last_esp32
        t_last_esp32 = time.time()
        if line == "OBSTACLE_ON":
            state["modes"]["obstacle"] = True
            state["modes"]["ultra_report"] = False  # ESP32 互斥
        elif line == "OBSTACLE_OFF":
            state["modes"]["obstacle"] = False
        elif line == "ULTRA_REPORT_ON":
            state["modes"]["ultra_report"] = True
            state["modes"]["obstacle"] = False  # ESP32 自动关掉本地避障
        elif line == "ULTRA_REPORT_OFF":
            state["modes"]["ultra_report"] = False
        elif line == "AUTO_OBSTACLE_BLOCKED_ULTRA_REPORT_ON":
            # ESP32 拒绝开避障:超声波上报占用中
            state["modes"]["obstacle"] = False
        elif line == "EMERGENCY_STOP":
            state["modes"]["emergency"] = True
            state["modes"]["obstacle"] = False
            state["modes"]["ultra_report"] = False
        elif line.startswith("BUTTON_SPEED,"):
            try:
                bs = int(line.split(",", 1)[1])
                state["speed"] = bs
                if minimap is not None:
                    minimap.set_button_speed(bs)
            except Exception: pass
        elif line == "3WD-ROBOT-START":
            # ESP32 刚刚启动(物理复位 / RTS pulse / USB 重插)→ 3 秒后自动重启用 ULTRA_REPORT
            # 3 秒给 BluePad32 蓝牙栈 init 时间,避免命令撞 ESP32 init phase 被丢
            push_log("ESP32 boot signal detected, re-enabling ULTRA_REPORT in 3s", "sys")
            # 顺便清死锁标志(RTS 自动恢复成功的标志)
            if state.get("esp32_deadlock"):
                state["esp32_deadlock"] = False
                bcast({"type": "state", "data": state})
            def _reapply():
                try:
                    if car is not None:
                        car.ultrasonic_report_on()
                except Exception as e:
                    push_log(f"auto re-enable ultra_report failed: {e}", "sys")
            threading.Timer(3.0, _reapply).start()

        if not line.startswith("ULTRA,"):
            push_log(line, "esp32")
            bcast({"type": "state", "data": state})

        orig_handle(line)

    car._handle_esp32_line = wrapped_handle

    orig_send = car.send
    cmd_to_action = {
        "x": "FORWARD", "w": "BACKWARD", "d": "LEFT", "a": "RIGHT",
        "c": "ROT_CCW", "z": "ROT_CW", "s": "STOP",
        "o": "OBSTACLE_ON", "p": "OBSTACLE_OFF",
        "u": "ULTRA_REPORT_ON", "v": "ULTRA_REPORT_OFF",
        "e": "EMERGENCY_STOP",
        "+": "SPEED_UP", "-": "SPEED_DOWN", "m": "SPEED_RESET",
        "1": "PLAY_CAT_1", "2": "PLAY_CAT_2", "3": "PLAY_CAT_3", "4": "PLAY_CAT_4",
    }

    def wrapped_send(c: str):
        # ====== 软件避障:OBSTACLE 命令转 ULTRA_REPORT + 设软避障标志 ======
        # ESP32 firmware 把 OBSTACLE 和 ULTRA_REPORT 设成互斥,开 OBSTACLE 时:
        #  - ESP32 不推 ULTRA 数据(UI 看不到距离/mini-map 没源)
        #  - ESP32 自主控制车(wrapped_send 看不到方向 → minimap 记不到轨迹)
        # 我们改成软件避障:实际启用 ULTRA_REPORT,服务端 on_ultra 检测障碍 → 发 stop。
        if c == "o":
            push_log("OBSTACLE_ON → soft-avoid (ULTRA_REPORT + service-side stop)", "cmd")
            state["modes"]["obstacle"] = True
            state["modes"]["ultra_report"] = True  # 软避障内部用 ULTRA_REPORT
            state["last_action"] = "OBSTACLE_ON"
            bcast({"type": "state", "data": state})
            # 内部发 'u' 切到 ULTRA_REPORT
            try: orig_send("u")
            except Exception as e: push_log(f"soft-avoid enable failed: {e}", "sys")
            return
        if c == "p":
            push_log("OBSTACLE_OFF (soft-avoid disabled, ULTRA_REPORT 保留)", "cmd")
            state["modes"]["obstacle"] = False
            state["last_action"] = "OBSTACLE_OFF"
            bcast({"type": "state", "data": state})
            # 不发 'p' 也不关 ultra(让数据持续)
            return

        action = cmd_to_action.get(c, f"SEND({c})")
        state["last_action"] = action
        if action == "EMERGENCY_STOP":
            state["modes"]["emergency"] = True
            state["modes"]["obstacle"] = False  # 急停同时清软避障
        elif action != "STOP":
            state["modes"]["emergency"] = False
        push_log(f"tx: {action}", "cmd")
        bcast({"type": "state", "data": state})
        if minimap is not None:
            if action in ("FORWARD","BACKWARD","LEFT","RIGHT","ROT_CW","ROT_CCW"):
                minimap.set_action(action)
            elif action in ("STOP", "EMERGENCY_STOP"):
                minimap.set_action(None)
        try:
            orig_send(c)
        except Exception as e:
            push_log(f"send fail {action}: {e}", "sys")

    car.send = wrapped_send

    # 包装 car.play_wav (cat1-4 走的就是它),播放前先停车减少 USB 总电流
    orig_play_wav = car.play_wav
    def wrapped_play_wav(wav_path):
        _audio_preflight()
        return orig_play_wav(wav_path)
    car.play_wav = wrapped_play_wav


# ====================== Lifespan ======================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global car, audio, camera, yolo, minimap, holder, recorder, loop
    loop = asyncio.get_running_loop()
    holder = HoldController()
    recorder = RecordingController()

    push_log("boot: initializing CarController", "sys")
    car = None
    for attempt in range(3):
        try:
            car = CarController(
                ultrasonic_callback=on_ultra,
                ps3_l1_callback=on_ps3_l1,
                ps3_l2_callback=on_ps3_l2,
            )
            install_event_hooks()
            push_log(f"boot: car ready (attempt {attempt+1})", "sys")
            break
        except Exception as e:
            push_log(f"boot: car init attempt {attempt+1} failed: {e}", "sys")
            await asyncio.sleep(3)
    if car is not None:
        # 等 5s 给 ESP32 BluePad32 蓝牙栈充分 init,降低首次 'u' 撞 init phase 导致丢命令的概率
        # 如果还失败,_ultra_keepalive 会接管 retry。
        try:
            await asyncio.sleep(5.0)
            car.ultrasonic_report_on()
            push_log("boot: sent ULTRA_REPORT_ON after 5s wait", "sys")
        except Exception as e:
            push_log(f"boot: initial ultra_report send failed: {e}", "sys")

    push_log("boot: initializing AudioController", "sys")
    try:
        audio = AudioController()
        push_log("boot: audio ready", "sys")
    except Exception as e:
        push_log(f"boot: audio FAILED: {e}", "sys")
        audio = None

    push_log("boot: initializing Cameras (multi)", "sys")
    # 多 cam:依次 probe /dev/video0..9 找能 open + 1280x720 MJPG 的 USB UVC 设备,
    # 按 enum 顺序赋 id a/b/c...。挂掉的 cam 在 cameras dict 里值为 None。
    cameras = {}
    cam_ids = []
    probe_paths = [f"/dev/video{i}" for i in range(0, 10)]
    for path in probe_paths:
        if not os.path.exists(path): continue
        # 跳过 RPi 自带 ISP 节点(只关心 USB UVC,/dev/video0 通常是 USB)
        if any(p in path for p in []): continue
        # 简单 probe:试着 open + 读一帧;成功就当成可用 cam
        try:
            test_cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
            ok = test_cap.isOpened() and test_cap.read()[0]
            test_cap.release()
            if not ok: continue
        except Exception: continue
        cid = chr(ord('a') + len(cam_ids))
        cam_ids.append(cid)
        try:
            # 两个 cam 分别接 RPi 不同 USB controller(Bus 01 / Bus 03)各自独立 480M
            # 带宽,可以同时 640x480@12fps。同 hub / 同 controller 上跑会 isoch 死锁
            cs = CameraStreamer(device=path, width=640, height=480, fps=12, quality=65)
            if cs.start():
                cameras[cid] = cs
                push_log(f"boot: cam_{cid} ready ({path} 640x480@12)", "sys")
            else:
                push_log(f"boot: cam_{cid} FAILED ({path}): {cs.error}", "sys")
                cameras[cid] = None
        except Exception as e:
            push_log(f"boot: cam_{cid} FAILED ({path}): {e}", "sys")
            cameras[cid] = None
        if len(cam_ids) >= 4: break
    # 主 cam alias:默认第一个 alive
    app.state.cameras = cameras
    app.state.main_cam_id = next((i for i in cam_ids if cameras.get(i)), None)
    camera = cameras.get(app.state.main_cam_id) if app.state.main_cam_id else None
    if camera is None:
        push_log("boot: no camera detected", "sys")
    # restore persisted cam state (main_id + flipped per cam)
    try:
        _load_cam_state()
        flipped_summary = ",".join(f"{cid}={'flip' if cs.flipped else 'norm'}" for cid, cs in cameras.items() if cs)
        push_log(f"boot: cam state restored ({flipped_summary})", "sys")
    except Exception as e:
        push_log(f"boot: cam state load failed: {e}", "sys")

    push_log("boot: initializing YOLO", "sys")
    if not _yolo_ok:
        push_log("boot: YOLO disabled (ultralytics not installed in this python)", "sys")
    else:
        try:
            yolo = YOLOInferencer(
                model_path=DEFAULT_YOLO_MODEL,
                # 每次 loop 调时 dynamic 拿当前主 cam(切换 main_id 后 yolo 跟随切 source)。
                # 原来用 `lambda: camera.get_jpeg()` 闭包到 global camera 仍 work,但启动时
                # camera 可能是 None 走 fallback,且 global rebind 在某些 import order 下不稳。
                get_jpeg_fn=lambda: (_get_cam().get_jpeg() if _get_cam() is not None else (None, 0)),
                on_detection=lambda payload: bcast({"type": "yolo", "data": payload}),
            )
            if yolo.start():
                if yolo.load_state():
                    push_log(f"boot: YOLO ready, restored state ({'ON' if yolo.enabled else 'OFF'}, conf={yolo.conf}, imgsz={yolo.imgsz})", "sys")
                else:
                    push_log(f"boot: YOLO ready ({DEFAULT_YOLO_MODEL}, disabled by default)", "sys")
            else:
                push_log(f"boot: YOLO load failed: {yolo.load_error}", "sys")
                yolo = None
        except Exception as e:
            push_log(f"boot: YOLO FAILED: {e}", "sys")
            yolo = None

    # ---- 录音超时:5 分钟自动 stop,防止前端断开/标签关闭导致 arecord 孤儿 ----
    async def _rec_timeout_watcher():
        while True:
            try:
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                return
            if not state.get("recording"): continue
            start = state.get("rec_start_t")
            if start is None: continue
            elapsed = time.time() - start
            if elapsed > MAX_RECORDING_DURATION_S and audio is not None:
                push_log(f"audio: auto-stop after {int(elapsed)}s (max {MAX_RECORDING_DURATION_S}s)", "audio")
                try:
                    p = audio.stop_recording()
                    state["recording"] = False
                    state["rec_path"] = None
                    state["rec_start_t"] = None
                    bcast({"type": "state", "data": state})
                except Exception as e:
                    push_log(f"audio auto-stop err: {e}", "sys")
    _rt_task = asyncio.create_task(_rec_timeout_watcher())

    # ---- ULTRA_REPORT keep-alive + 自动恢复 (RTS pulse → ESP32 EN reset) ----
    # 流程:
    #   1) ESP32 沉默 > 12s → 温和重发 'u'
    #   2) 连续 3 次重发都沉默 → 设 esp32_deadlock=True + 自动尝试 RTS pulse 让 ESP32 cold reset
    #      (yahboom 板 CH340 RTS 通常经反相接 ESP32 EN,toggle 等于按 RST 按钮)
    #   3) 等 8s 给 ESP32 boot,如果还是沉默 → 再 pulse 一次(最多 3 次)
    #   4) 3 次自动都救不回 → banner 引导用户关车电池(物理层最后兜底)
    async def _esp32_rts_pulse():
        """RTS 脉冲模拟按 ESP32 RST 按钮。需要 yahboom 板把 CH340 RTS 接 ESP32 EN(经反相电路)。"""
        if car is None or car.ser is None or not car.ser.is_open:
            return False
        try:
            # 序列:DTR 保持 release(IO0=HIGH 防止进 download mode)
            #       RTS pulse LOW→HIGH→LOW 触发 EN reset
            car.ser.setDTR(False)
            car.ser.setRTS(True)    # EN=LOW (reset 触发)
            await asyncio.sleep(0.12)
            car.ser.setRTS(False)   # EN=HIGH 释放,ESP32 开始 boot
            return True
        except Exception as e:
            push_log(f"RTS pulse failed: {e}", "sys")
            return False

    async def _ultra_keepalive():
        last_reattempt = 0
        REATTEMPT_INTERVAL = 10.0
        ESP32_SILENT_THRESHOLD_MS = 12000
        DEADLOCK_AFTER_ATTEMPTS = 3
        MAX_AUTO_RESET = 3
        consec_attempts = 0
        auto_reset_count = 0
        post_reset_wait_until = 0
        while True:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                return
            if car is None: continue
            if state["modes"]["obstacle"] or state["modes"]["emergency"]: continue
            # 如果在 reset 之后还在等 ESP32 boot 起来,跳过这一轮
            if time.time() < post_reset_wait_until: continue
            age_ms = (time.time() - t_last_esp32) * 1000 if t_last_esp32 else 99999
            if age_ms < ESP32_SILENT_THRESHOLD_MS:
                # ESP32 在喘气 → 清死锁标志 + 计数器
                consec_attempts = 0
                auto_reset_count = 0
                if state["esp32_deadlock"]:
                    state["esp32_deadlock"] = False
                    push_log("ESP32 alive again, deadlock cleared", "sys")
                    bcast({"type": "state", "data": state})
                continue
            now = time.time()
            if now - last_reattempt < REATTEMPT_INTERVAL: continue
            last_reattempt = now
            consec_attempts += 1
            push_log(f"keep-alive #{consec_attempts}: ESP32 silent {int(age_ms)}ms, resending 'u'", "sys")
            try: car.ultrasonic_report_on()
            except Exception as e:
                push_log(f"keep-alive resend failed: {e}", "sys")

            if consec_attempts >= DEADLOCK_AFTER_ATTEMPTS:
                if not state["esp32_deadlock"]:
                    state["esp32_deadlock"] = True
                    push_log(f"ESP32 DEADLOCK detected ({consec_attempts} silent retries)", "sys")
                    bcast({"type": "state", "data": state})
                # 自动 RTS 恢复:最多尝试 MAX_AUTO_RESET 次
                if auto_reset_count < MAX_AUTO_RESET:
                    auto_reset_count += 1
                    push_log(f"auto-recovery #{auto_reset_count}/{MAX_AUTO_RESET}: pulsing RTS to reset ESP32", "sys")
                    ok = await _esp32_rts_pulse()
                    if ok:
                        # 给 ESP32 8s 跑完 boot + BluePad32 init
                        post_reset_wait_until = time.time() + 8
                        # boot 完后会自动收到 3WD-ROBOT-START → wrapped_handle 触发 ultra_report_on
                else:
                    # 自动恢复用完了,只能靠用户物理操作 — banner 已经在
                    pass

    _ka_task = asyncio.create_task(_ultra_keepalive())

    # 把 reset 函数暴露给手动 endpoint 用
    app.state.esp32_rts_pulse = _esp32_rts_pulse

    # ---- 机械臂可选初始化 ----
    global arm
    if not _arm_ok:
        push_log("boot: arm disabled (physical_agent not installed)", "sys")
    elif not os.path.exists(ARM_CONFIG_PATH):
        push_log(f"boot: arm disabled (config missing at {ARM_CONFIG_PATH})", "sys")
    else:
        try:
            arm_ctrl = Sts3215ArmController.from_json(ARM_CONFIG_PATH)
            arm_ctrl.connect()
            arm = arm_ctrl
            push_log("boot: arm ready", "sys")
        except Exception as e:
            push_log(f"boot: arm init failed: {e}", "sys")
            arm = None

    push_log("boot: starting MiniMap (dead-reckoning + occupancy)", "sys")
    minimap = MiniMap()
    minimap.start()
    push_log("boot: minimap ready (memory only, resets on restart)", "sys")

    # WS push minimap state every 200ms while there are clients
    async def _minimap_pusher():
        last = 0
        while True:
            await asyncio.sleep(0.2)
            if not clients or minimap is None: continue
            try: await _broadcast({"type": "map", "data": minimap.snapshot()})
            except Exception: pass
    _mm_task = asyncio.create_task(_minimap_pusher())

    # WS push recorder state every 200ms (录制/回放进度;静默时也 push 让 UI 状态同步)
    async def _recorder_pusher():
        while True:
            await asyncio.sleep(0.2)
            if not clients or recorder is None: continue
            try: await _broadcast({"type": "rec", "data": recorder.status()})
            except Exception: pass
    _rec_task = asyncio.create_task(_recorder_pusher())

    push_log("system online", "sys")
    yield
    push_log("system shutdown", "sys")
    _mm_task.cancel()
    _rt_task.cancel()
    _ka_task.cancel()
    _rec_task.cancel()

    # 每个 obj.close() 包到独立 thread,5s timeout,防止某个 close 阻塞 → SIGKILL → 留 driver state 尾巴
    def _safe_close(obj, name):
        try:
            (obj.close() if hasattr(obj, "close") else obj.stop())
        except Exception as e:
            print(f"[shutdown] {name} close error: {e}")

    # car 优先 close,确保 ser.close() 在 SIGKILL 前完成
    # arm.disconnect() 先做(servo disable_torque 需要时间)
    if arm is not None:
        try: arm.disconnect()
        except Exception: pass
    cam_close_list = [(cs, f"cam_{cid}") for cid, cs in (getattr(app.state, "cameras", {}) or {}).items() if cs]
    for obj, name in [(car, "car"), (audio, "audio")] + cam_close_list + [(yolo, "yolo"), (minimap, "minimap")]:
        if obj is None: continue
        t = threading.Thread(target=_safe_close, args=(obj, name), daemon=True)
        t.start()
        t.join(timeout=5)
        if t.is_alive():
            print(f"[shutdown] {name} close took >5s, skipping")


API_DESCRIPTION = """
# 🤖 ROBOT CONTROL UNIT // RPI-01 — HTTP API

封装 yahboom 3WD 全向轮机器人小车的所有数据与控制能力,供本机其他程序调用。

## 端点分类
- **`/api/cmd/{action}`** — 单条 POST 控制车辆/避障/音效/速度
- **`/api/state`** — 拉取当前完整状态(模式 / 距离 / 速度 / 最后动作 / 录音 / 日志)
- **`/api/health`** — 模块连通性 + 系统(CPU 温度 / 负载 / 内存 / 风扇 RPM&PWM)
- **`/api/audio/*`** — 录音 / 播放 / 文件列表
- **`/api/camera/*`** — MJPEG 流 / 单帧快照
- **`/ws`** — WebSocket 实时推送 (ultra / state / log)

## WebSocket 推送消息格式
- `{"type":"snapshot","data":{"state":{...},"logs":[...]}}` — 首次连上的全量快照
- `{"type":"ultra","data":{1:{distance_mm,has_obstacle,t},2:{...},3:{...}}}` — 超声波数据
- `{"type":"state","data":{...}}` — 状态变化
- `{"type":"log","data":{t,kind,line}}` — 单条日志(kind: esp32/cmd/audio/ps3/sys)

## 完整 Markdown 文档:`/home/pi/Desktop/API.md`
"""

app = FastAPI(
    title="Robot Control Unit",
    version="1.0",
    description=API_DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# ====================== HTTP ======================

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    # no-store 让浏览器每次刷新都拉新 HTML,deploy 后无需用户 hard refresh
    return HTMLResponse(INDEX_HTML, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
    })


@app.get("/api/arm/status", tags=["arm"], summary="机械臂状态(关节角度 / 末端 xyz)")
async def arm_status():
    if arm is None:
        reason = "physical_agent missing" if not _arm_ok else \
                 (f"config missing: {ARM_CONFIG_PATH}" if not os.path.exists(ARM_CONFIG_PATH) else "init failed")
        return {"available": _arm_ok, "connected": False, "reason": reason}
    def _read():
        with arm_lock:
            return arm.read_state()
    try:
        st = await asyncio.to_thread(_read)
        return {"available": True, "connected": arm.is_connected, **st}
    except Exception as e:
        return {"available": True, "connected": False, "error": str(e)}


@app.post("/api/arm/home", tags=["arm"], summary="回 HOME 位置",
          description=("HOME 优先级:1) arm_meta.json 的 `_home_positions_deg` 用户标定 2) 启动时 startup_positions_deg 3) 全 0\n"
                       "query `?mode=zero` 强制全 0 / `?mode=startup` 启动位置 / 默认 user (meta 里的)"
                       "body 可选 `speed_deg_s` 覆盖默认速度。"))
async def arm_home(req: Request, mode: str = "user"):
    if arm is None: raise HTTPException(503, "arm not ready")
    body = await req.json() if int(req.headers.get("content-length", "0") or 0) > 0 else {}
    speed = body.get("speed_deg_s")
    # 从 arm_meta.json 读 _home_positions_deg
    meta_path = os.path.expanduser("~/Desktop/arm_meta.json")
    user_home = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f: m = json.load(f)
            user_home = m.get("_home_positions_deg") or {}
        except Exception: pass
    def _do():
        with arm_lock:
            online = set(arm.online_joint_names or [])
            if mode == "zero":
                return arm.home(speed_deg_s=speed)
            # user / startup 都按 online 过滤,跳过 missing servo(防止 J6 死时 home 整体失败)
            raw = (user_home if (mode == "user" and user_home) else (arm.startup_positions_deg or {}))
            targets = {k: v for k, v in raw.items() if k in online}
            if not targets:
                return arm.home(speed_deg_s=speed)
            return arm.move_joints(targets, speed_deg_s=speed)
    try:
        result = await asyncio.to_thread(_do)
        effective = (user_home if mode == "user" and user_home else
                     (arm.startup_positions_deg if mode != "zero" else {n: 0.0 for n in (arm.startup_positions_deg or {})}))
        return {"ok": True, "mode": mode, "targets": effective, "goals": result}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/arm/reset", tags=["arm"], summary="重置 arm controller(脱限位救援)",
          description="disconnect 当前 controller → 重新 from_json 加载 config → connect()。会触发 servo 重新 configure + 读取 startup positions。不清 servo 内部 multi-turn 累计计数(要那个必须断电重启 servo bus)。")
async def arm_reset():
    global arm
    if Sts3215ArmController is None: raise HTTPException(503, "controller class not available")
    def _do():
        global arm
        # 不持 arm_lock(可能旧 controller 已 stuck),直接换新实例
        old = arm
        if old is not None:
            try: old.disconnect()
            except Exception: pass
        try:
            new_ctrl = Sts3215ArmController.from_json(ARM_CONFIG_PATH)
            new_ctrl.connect()
            arm = new_ctrl
            return new_ctrl.startup_positions_deg
        except Exception as e:
            arm = None
            raise
    try:
        positions = await asyncio.to_thread(_do)
        push_log(f"arm reset → connected, positions: {positions}", "sys")
        return {"ok": True, "positions": positions}
    except Exception as e:
        push_log(f"arm reset FAILED: {e}", "sys")
        raise HTTPException(500, str(e))


@app.post("/api/arm/save_home", tags=["arm"], summary="把当前位置存为 HOME 目标",
          description="把所有 joint 当前 present_position 写到 arm_meta.json 的 `_home_positions_deg`,以后 /api/arm/home (默认 mode=user) 会回这里。")
async def arm_save_home():
    if arm is None: raise HTTPException(503, "arm not ready")
    meta_path = os.path.expanduser("~/Desktop/arm_meta.json")
    def _do():
        with arm_lock:
            return arm.read_present_positions_deg()
    try:
        positions = await asyncio.to_thread(_do)
        m = {}
        if os.path.exists(meta_path):
            with open(meta_path) as f: m = json.load(f)
        m["_home_positions_deg"] = {k: round(float(v), 2) for k, v in positions.items()}
        with open(meta_path, "w") as f: json.dump(m, f, indent=2, ensure_ascii=False)
        push_log(f"home position saved: {m['_home_positions_deg']}", "sys")
        return {"ok": True, "home_positions_deg": m["_home_positions_deg"]}
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ Arm Preset Slots + Loop ============
_arm_loop_state = {"running": False, "thread": None, "cur_idx": None}

def _arm_meta_read():
    p = os.path.expanduser("~/Desktop/arm_meta.json")
    if not os.path.exists(p): return p, {}
    try:
        with open(p) as f: return p, json.load(f)
    except Exception: return p, {}

def _arm_meta_write(p, m):
    with open(p, "w") as f: json.dump(m, f, indent=2, ensure_ascii=False)


@app.get("/api/arm/presets", tags=["arm"], summary="所有 5 个预设槽位状态")
async def arm_presets():
    _, m = _arm_meta_read()
    slots = m.get("_preset_slots") or {}
    return {"slots": [slots.get(str(i)) for i in range(5)]}


@app.post("/api/arm/preset/{idx}/save", tags=["arm"], summary="把当前 pose 存到 slot idx (0-4)")
async def arm_preset_save(idx: int, req: Request):
    if arm is None: raise HTTPException(503, "arm not ready")
    if idx < 0 or idx >= 5: raise HTTPException(400, "idx must be 0-4")
    body = {}
    try:
        if int(req.headers.get("content-length", "0") or 0) > 0:
            body = await req.json()
    except Exception: pass
    label = body.get("label") or f"Slot {idx+1}"
    def _do():
        with arm_lock:
            return arm.read_present_positions_deg()
    positions = await asyncio.to_thread(_do)
    p, m = _arm_meta_read()
    slots = m.get("_preset_slots") or {}
    slots[str(idx)] = {
        "label": label,
        "positions_deg": {k: round(float(v), 2) for k, v in positions.items()},
    }
    m["_preset_slots"] = slots
    _arm_meta_write(p, m)
    push_log(f"arm preset[{idx}] '{label}' saved: {len(positions)} joints", "sys")
    return {"ok": True, "idx": idx, "slot": slots[str(idx)]}


@app.post("/api/arm/preset/{idx}/move", tags=["arm"], summary="move 到 slot idx",
          description="query `?speed_deg_s=180` 控制速度。默认 180°/s 比 controller 的 default 10°/s 快很多")
async def arm_preset_move(idx: int, speed_deg_s: float = 180):
    if arm is None: raise HTTPException(503, "arm not ready")
    if idx < 0 or idx >= 5: raise HTTPException(400, "idx must be 0-4")
    _, m = _arm_meta_read()
    slot = (m.get("_preset_slots") or {}).get(str(idx))
    if not slot: raise HTTPException(404, f"slot {idx} empty")
    online = set(arm.online_joint_names or [])
    targets = {k: v for k, v in (slot.get("positions_deg") or {}).items() if k in online}
    if not targets: raise HTTPException(400, "no online joints in slot")
    def _do():
        with arm_lock:
            return arm.move_joints(targets, speed_deg_s=speed_deg_s)
    try:
        goals = await asyncio.to_thread(_do)
        return {"ok": True, "idx": idx, "moved": targets, "speed_deg_s": speed_deg_s, "goals": goals}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/arm/preset/{idx}/clear", tags=["arm"], summary="清空 slot idx")
async def arm_preset_clear(idx: int):
    if idx < 0 or idx >= 5: raise HTTPException(400, "idx must be 0-4")
    p, m = _arm_meta_read()
    slots = m.get("_preset_slots") or {}
    if slots.pop(str(idx), None) is not None:
        m["_preset_slots"] = slots
        _arm_meta_write(p, m)
    return {"ok": True, "idx": idx}


@app.post("/api/arm/preset_loop/start", tags=["arm"], summary="循环遍历 slots",
          description=('body: `{indices: [0,1,2,3], interval_ms: 1000, speed_deg_s: 180, cycles: 0}`\n'
                       'cycles=0 → 无限循环。只 include 已 save 的 slot,自动跳过 empty。'))
async def arm_preset_loop_start(req: Request):
    if arm is None: raise HTTPException(503, "arm not ready")
    body = await req.json() if int(req.headers.get("content-length", "0") or 0) > 0 else {}
    raw_indices = body.get("indices") or [0, 1, 2, 3, 4]
    interval_ms = max(50, int(body.get("interval_ms", 1000)))
    speed = float(body.get("speed_deg_s", 180))
    cycles = int(body.get("cycles", 0))
    _, m = _arm_meta_read()
    slots_dict = m.get("_preset_slots") or {}
    indices = [i for i in raw_indices if str(i) in slots_dict]
    if not indices: raise HTTPException(400, "no saved slot in given indices")
    online = set(arm.online_joint_names or [])

    # stop previous loop (if running)
    _arm_loop_state["running"] = False
    prev = _arm_loop_state.get("thread")
    if prev and prev.is_alive():
        prev.join(timeout=2.0)

    def _loop():
        cycle = 0
        push_log(f"arm preset loop: indices={indices} interval={interval_ms}ms speed={speed}°/s cycles={cycles or '∞'}", "sys")
        try:
            while _arm_loop_state["running"]:
                for i in indices:
                    if not _arm_loop_state["running"]: break
                    slot = slots_dict.get(str(i))
                    if not slot: continue
                    targets = {k: v for k, v in (slot.get("positions_deg") or {}).items() if k in online}
                    if not targets: continue
                    try:
                        with arm_lock:
                            arm.move_joints(targets, speed_deg_s=speed)
                    except Exception as e:
                        push_log(f"arm loop slot[{i}] err: {e}", "sys")
                    _arm_loop_state["cur_idx"] = i
                    time.sleep(interval_ms / 1000.0)
                cycle += 1
                if cycles and cycle >= cycles: break
        finally:
            _arm_loop_state["running"] = False
            _arm_loop_state["cur_idx"] = None
            push_log(f"arm preset loop ended after {cycle} cycle(s)", "sys")

    _arm_loop_state["running"] = True
    th = threading.Thread(target=_loop, daemon=True)
    _arm_loop_state["thread"] = th
    th.start()
    return {"ok": True, "indices": indices, "interval_ms": interval_ms, "speed_deg_s": speed, "cycles": cycles}


@app.post("/api/arm/preset_loop/stop", tags=["arm"], summary="停止循环")
async def arm_preset_loop_stop():
    _arm_loop_state["running"] = False
    return {"ok": True}


@app.get("/api/arm/preset_loop/status", tags=["arm"], summary="循环状态")
async def arm_preset_loop_status():
    return {"running": _arm_loop_state["running"], "cur_idx": _arm_loop_state["cur_idx"]}


@app.get("/api/arm/meta", tags=["arm"], summary="关节元信息(描述 + 限位 + 校准状态)",
         description="UI 用:从 arm_config.json (joints/limits) + arm_meta.json (人类描述/calibrated) 拼。meta 文件可独立编辑,不影响 controller 加载。")
async def arm_meta():
    if arm is None: raise HTTPException(503, "arm not ready")
    cfg_path = os.path.expanduser("~/Desktop/arm_config.json")
    meta_path = os.path.expanduser("~/Desktop/arm_meta.json")
    try:
        with open(cfg_path) as f: cfg = json.load(f)
    except Exception as e:
        raise HTTPException(500, f"read config failed: {e}")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f: meta = json.load(f)
        except Exception: pass
    out = []
    for j in cfg.get("joints", []):
        name = j["name"]
        m = meta.get(name) or {}
        out.append({
            "name": name,
            "description": m.get("description", ""),
            "calibrated": bool(m.get("calibrated", False)),
            "min_deg": j.get("min_position_deg"),
            "max_deg": j.get("max_position_deg"),
            "motor_id": j.get("motor_id"),
        })
    return {"joints": out, "startup_positions_deg": arm.startup_positions_deg}


@app.post("/api/arm/nudge_cartesian", tags=["arm"], summary="末端笛卡尔增量移动(米)",
          description="body: `{dx: float (m), dy: float, dz: float, speed_deg_s?: float}`。例: `{\"dx\": 0.01}` 沿 +x 走 10mm")
async def arm_nudge_cartesian(req: Request):
    if arm is None: raise HTTPException(503, "arm not ready")
    body = await req.json()
    dx = float(body.get("dx", 0))
    dy = float(body.get("dy", 0))
    dz = float(body.get("dz", 0))
    speed = body.get("speed_deg_s")
    def _do():
        with arm_lock:
            return arm.nudge_cartesian(dx, dy, dz, speed_deg_s=speed)
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/arm/move_cartesian", tags=["arm"], summary="末端笛卡尔绝对位置(米)",
          description="body: `{x, y, z, speed_deg_s?}`")
async def arm_move_cartesian(req: Request):
    if arm is None: raise HTTPException(503, "arm not ready")
    body = await req.json()
    def _do():
        with arm_lock:
            return arm.move_cartesian(
                float(body["x"]), float(body["y"]), float(body["z"]),
                speed_deg_s=body.get("speed_deg_s"),
            )
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/arm/nudge_joint", tags=["arm"], summary="单关节增量",
          description="body: `{joint: 'joint_1', delta_deg: float, speed_deg_s?: float}`. 默认 speed 180°/s 比 controller config 10°/s 快很多")
async def arm_nudge_joint(req: Request):
    if arm is None: raise HTTPException(503, "arm not ready")
    body = await req.json()
    speed = body.get("speed_deg_s") or 180
    def _do():
        with arm_lock:
            return arm.nudge_joint(body["joint"], float(body["delta_deg"]), speed_deg_s=speed)
    try:
        goal = await asyncio.to_thread(_do)
        return {"ok": True, "joint": body["joint"], "goal_raw": goal}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/arm/move_joint", tags=["arm"], summary="单关节绝对角度",
          description="body: `{joint: 'joint_1', target_deg: float, speed_deg_s?: float}`. 默认 180°/s")
async def arm_move_joint(req: Request):
    if arm is None: raise HTTPException(503, "arm not ready")
    body = await req.json()
    speed = body.get("speed_deg_s") or 180
    def _do():
        with arm_lock:
            return arm.move_joint(body["joint"], float(body["target_deg"]), speed_deg_s=speed)
    try:
        goal = await asyncio.to_thread(_do)
        return {"ok": True, "joint": body["joint"], "goal_raw": goal}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/arm/torque", tags=["arm"], summary="开/关 servo torque(锁紧)",
          description='body: `{on: true/false}`. 关 → 可徒手扳动;开 → servo 锁紧持位且响应 move 命令')
async def arm_torque(req: Request):
    if arm is None or arm.bus is None: raise HTTPException(503, "arm not ready")
    body = await req.json()
    on = bool(body.get("on", True))
    def _do():
        with arm_lock:
            if on: arm.bus.enable_torque()
            else: arm.bus.disable_torque()
    try:
        await asyncio.to_thread(_do)
        return {"ok": True, "torque_on": on}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/arm/gripper", tags=["arm"], summary="夹爪开/合(增量)",
          description="body: `{delta_deg: float, speed_deg_s?: float}`。正值通常 open,负值 close。")
async def arm_gripper(req: Request):
    if arm is None: raise HTTPException(503, "arm not ready")
    body = await req.json()
    def _do():
        with arm_lock:
            return arm.nudge_gripper(float(body.get("delta_deg", 5)), speed_deg_s=body.get("speed_deg_s"))
    try:
        goal = await asyncio.to_thread(_do)
        return {"ok": True, "goal_raw": goal}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/system/esp32_reset", tags=["state"], summary="用 RTS 脉冲 cold-reset ESP32 (等价按板上 RST 按钮)",
          description="yahboom 板 CH340 RTS 通常经反相接 ESP32 EN。toggle 让 ESP32 重启 firmware,不进 download mode。如果板子没接这个引脚,无效但无害。")
async def system_esp32_reset(req: Request):
    if car is None or car.ser is None or not car.ser.is_open:
        raise HTTPException(503, "serial port not open")
    pulse = getattr(app.state, "esp32_rts_pulse", None)
    if pulse is None:
        raise HTTPException(503, "rts pulse helper not initialized")
    ok = await pulse()
    push_log(f"manual ESP32 RTS pulse: {'ok' if ok else 'failed'}", "sys")
    return {"ok": ok, "msg": "RTS pulsed; ESP32 booting (5-10s); ULTRA_REPORT will auto re-enable on 3WD-ROBOT-START"}


@app.post("/api/system/restart", tags=["state"], summary="重启 robot-console.service",
          description="模块炸掉时一键重启服务(systemctl restart)。Popen 触发后 systemctl 通过 dbus 让 systemd kill+restart 本进程,客户端 WS 会断开然后自动重连。")
async def system_restart():
    import subprocess as _sp
    push_log("system: restart requested via API", "sys")
    try:
        _sp.Popen(
            ["sudo", "systemctl", "restart", "robot-console"],
            stdin=_sp.DEVNULL, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        raise HTTPException(500, f"restart failed: {e}")
    return {"ok": True, "msg": "systemctl restart issued; service will restart in ~3s"}


@app.get("/api/state", tags=["state"], summary="拉取当前完整状态 + 最近 80 条日志",
         description="返回 `state` 对象 (ultra / modes / speed / last_action / recording) 和最近的日志数组")
async def get_state():
    return {"state": state, "logs": list(log_buf)[-80:]}


def _cpu_temp_c() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        return None


def _fan_state() -> dict:
    """读 /sys/class/hwmon 找 pwmfan 设备,返回 rpm/pwm/duty_pct/cur_state。"""
    out = {"rpm": None, "pwm": None, "duty_pct": None, "cooling_state": None}
    try:
        for hwmon in os.listdir("/sys/class/hwmon"):
            base = f"/sys/class/hwmon/{hwmon}"
            try:
                with open(f"{base}/name") as f:
                    if f.read().strip() != "pwmfan":
                        continue
            except Exception:
                continue
            try:
                with open(f"{base}/fan1_input") as f:
                    out["rpm"] = int(f.read().strip())
            except Exception: pass
            try:
                with open(f"{base}/pwm1") as f:
                    p = int(f.read().strip())
                    out["pwm"] = p
                    out["duty_pct"] = round(100.0 * p / 255, 1)
            except Exception: pass
            break
        try:
            with open("/sys/class/thermal/cooling_device0/cur_state") as f:
                cur = int(f.read().strip())
            with open("/sys/class/thermal/cooling_device0/max_state") as f:
                mx = int(f.read().strip())
            out["cooling_state"] = f"{cur}/{mx}"
        except Exception: pass
    except Exception:
        pass
    return out


def _esp32_last_age_ms() -> int | None:
    if t_last_esp32 == 0.0:
        return None
    return int((time.time() - t_last_esp32) * 1000)


@app.get("/api/health", tags=["state"], summary="模块连通性 + 系统监控",
         description="serial/esp32/audio/camera/fan 每个模块 {ok, detail};system 含 cpu_temp_c, load_1m, mem_pct, fan_rpm, fan_pwm")
async def health():
    # serial
    serial_ok = False
    serial_detail = "—"
    if car is not None:
        try:
            serial_ok = bool(car.ser and car.ser.is_open)
            serial_detail = car.ser.port if car.ser else "—"
        except Exception:
            pass

    # esp32 last seen
    esp_age = _esp32_last_age_ms()
    esp_ok = esp_age is not None and esp_age < 3000

    # audio
    audio_ok = audio is not None
    audio_detail = audio.audio_device if audio else "—"

    # camera(多 cam:health 报"主"信息;详细每 cam 走 /api/camera/list)
    cams_d = getattr(app.state, "cameras", {}) or {}
    main_id = getattr(app.state, "main_cam_id", None)
    alive_ids = [cid for cid, cs in cams_d.items() if cs and cs.is_alive()]
    cam_ok = bool(alive_ids)
    if cams_d:
        cam_detail = f"main={main_id or '—'} · alive=[{','.join(alive_ids) or 'none'}]/{len(cams_d)}"
    else:
        cam_detail = "—"
    main_cam = cams_d.get(main_id) if main_id else None
    cam_fps = round(main_cam.fps(), 1) if main_cam else 0.0
    cam_age = None
    if main_cam and main_cam.last_frame_t:
        cam_age = int((time.time() - main_cam.last_frame_t) * 1000)

    # system
    cpu_temp = _cpu_temp_c()
    try:    load1 = round(os.getloadavg()[0], 2)
    except Exception: load1 = None
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                k, v = line.split(":", 1)
                meminfo[k.strip()] = v.strip()
            mem_total = int(meminfo["MemTotal"].split()[0])
            mem_avail = int(meminfo["MemAvailable"].split()[0])
            mem_pct = round(100.0 * (1 - mem_avail / mem_total), 1)
    except Exception:
        mem_pct = None
    up = int(time.time() - t_boot)
    fan = _fan_state()

    return {
        "modules": {
            "serial":  {"ok": serial_ok, "detail": serial_detail},
            "esp32":   {"ok": esp_ok, "detail": (f"last {esp_age}ms ago" if esp_age is not None else "no data")},
            "audio":   {"ok": audio_ok, "detail": audio_detail},
            "camera":  {"ok": cam_ok, "detail": cam_detail, "fps": cam_fps, "frame_age_ms": cam_age},
            "fan":     {"ok": fan["rpm"] is not None and fan["rpm"] > 0, "detail": (f"{fan['rpm']} rpm · {fan['duty_pct']}%" if fan["rpm"] is not None else "not present")},
            "yolo":    (
                {"ok": yolo.enabled and yolo.latest_inference_t > 0,
                 "detail": (f"{yolo.latest_inference_ms} ms · {len(yolo.latest_detections)} det"
                            if yolo.enabled else ("loaded · disabled" if yolo.model_loaded else "load error"))}
                if yolo else
                {"ok": False, "detail": ("ultralytics missing" if not _yolo_ok else "init failed")}
            ),
            "minimap": (
                {"ok": minimap.running,
                 "detail": (f"pose=({minimap.x_mm:.0f},{minimap.y_mm:.0f}) θ={_math.degrees(minimap.theta):.0f}° · "
                            f"{len(minimap.grid)} cells · {len(minimap.pose_history)} trail")}
                if minimap else {"ok": False, "detail": "not initialized"}
            ),
            "arm": (
                {"ok": arm.is_connected, "detail": f"{len(arm.online_joint_names)} joints online"}
                if arm else
                {"ok": False, "detail": ("physical_agent missing" if not _arm_ok else "not configured")}
            ),
        },
        "system": {
            "cpu_temp_c": cpu_temp,
            "load_1m": load1,
            "mem_pct": mem_pct,
            "uptime_s": up,
            "clients": len(clients),
            "fan_rpm": fan["rpm"],
            "fan_pwm": fan["pwm"],
            "fan_duty_pct": fan["duty_pct"],
            "fan_cooling_state": fan["cooling_state"],
        },
    }


CMD_MAP = {
    "forward":      lambda: car.forward(),
    "backward":     lambda: car.backward(),
    "left":         lambda: car.left(),
    "right":        lambda: car.right(),
    "rotate_cw":    lambda: car.rotate_cw(),
    "rotate_ccw":   lambda: car.rotate_ccw(),
    "stop":         lambda: car.stop(),
    "obstacle_on":  lambda: car.obstacle_on(),
    "obstacle_off": lambda: car.obstacle_off(),
    "ultra_on":     lambda: car.ultrasonic_report_on(),
    "ultra_off":    lambda: car.ultrasonic_report_off(),
    "emergency":    lambda: car.emergency_stop(),
    "speed_up":     lambda: car.speed_up(),
    "speed_down":   lambda: car.speed_down(),
    "speed_reset":  lambda: car.speed_reset(),
    "cat1":         lambda: car.play_cat_1(),
    "cat2":         lambda: car.play_cat_2(),
    "cat3":         lambda: car.play_cat_3(),
    "cat4":         lambda: car.play_cat_4(),
}


@app.post("/api/cmd/{action}", tags=["control"], summary="发送单条车辆/避障/音效/速度指令",
          description=("**有效 action**:\n\n"
                       "- 运动: `forward` `backward` `left` `right` `rotate_cw` `rotate_ccw` `stop`\n"
                       "- 避障: `obstacle_on` `obstacle_off`(软件避障,跟 ultra 兼容)\n"
                       "- 超声波上报: `ultra_on` `ultra_off`\n"
                       "- 急停: `emergency`\n"
                       "- 速度: `speed_up` `speed_down` `speed_reset`\n"
                       "- 音效: `cat1` `cat2` `cat3` `cat4`\n\n"
                       "**持续模式 query params**(优先级 angle > distance > duration):\n"
                       "- `?duration=N` 持续 N 秒(最长 60)\n"
                       "- `?distance=N` 行驶 N **mm**(仅 forward/backward/left/right,基于 minimap K_LINEAR + 当前 BUTTON_SPEED 换算)\n"
                       "- `?angle=N` 转动 N **度**(仅 rotate_cw/rotate_ccw,基于 minimap K_ANGULAR)\n\n"
                       "持续模式期间用后端 100ms 绝对时间步进给 ESP32 发 cmd,HTTP 阻塞直到完成。\n"
                       "例:`curl -X POST 'http://pi:8000/api/cmd/forward?distance=500'` 直走 500mm 自停。\n"))
async def cmd(action: str, duration: float = 0, distance: float = 0, angle: float = 0):
    if car is None:
        raise HTTPException(503, "car not ready")
    HOLD_LINEAR = {"forward","backward","left","right"}
    HOLD_ROTATE = {"rotate_cw","rotate_ccw"}
    HOLD_ACTIONS = HOLD_LINEAR | HOLD_ROTATE

    # 优先级:angle > distance > duration
    d_s = 0.0
    mode_detail = None
    if angle > 0:
        if action not in HOLD_ROTATE:
            raise HTTPException(400, f"angle only valid for rotate_cw / rotate_ccw")
        bs = state.get("speed") or 2000
        k_a = (minimap.K_ANGULAR if minimap else 0.0006)
        omega_deg_s = max(1.0, _math.degrees(k_a * bs))
        d_s = float(angle) / omega_deg_s
        mode_detail = f"angle={angle}° (est {omega_deg_s:.1f}°/s)"
    elif distance > 0:
        if action not in HOLD_LINEAR:
            raise HTTPException(400, f"distance only valid for forward/backward/left/right")
        bs = state.get("speed") or 2000
        k_l = (minimap.K_LINEAR if minimap else 0.05)
        ratio = (minimap.BACKWARD_RATIO if minimap else 0.7) if action == "backward" else 1.0
        v_mm_s = max(1.0, k_l * bs * ratio)
        d_s = float(distance) / v_mm_s
        mode_detail = f"distance={distance}mm (est {v_mm_s:.1f}mm/s, ratio×{ratio})"
    elif duration > 0:
        if action not in HOLD_ACTIONS:
            raise HTTPException(400, f"duration only valid for hold-able actions: {sorted(HOLD_ACTIONS)}")
        d_s = float(duration)
        mode_detail = f"duration={duration}s"

    if d_s > 0:
        if holder is None:
            raise HTTPException(503, "holder not ready")
        d_s = max(0.05, min(60.0, d_s))
        holder.hold(action)
        end = time.time() + d_s
        while time.time() < end:
            await asyncio.sleep(min(0.5, end - time.time()))
            holder.renew(action)
        holder.release(reason=f"{mode_detail} ended")
        return {"ok": True, "action": action, "actual_duration_s": round(d_s, 3), "mode": "hold", "detail": mode_detail}

    fn = CMD_MAP.get(action)
    if not fn:
        raise HTTPException(404, f"unknown action: {action}")
    try:
        fn()
    except Exception as e:
        push_log(f"cmd error: {action}: {e}", "sys")
        raise HTTPException(500, str(e))
    if recorder is not None: recorder.log_event("cmd", {"action": action})
    return {"ok": True, "action": action, "mode": "single"}


@app.get("/api/map/state", tags=["map"], summary="MiniMap pose + 占用栅格快照",
         description="dead-reckoning 估算的 pose (x,y mm + theta rad) + 累积的 occupancy cells")
async def map_state():
    if minimap is None:
        raise HTTPException(503, "minimap not initialized")
    return minimap.snapshot()


@app.post("/api/map/reset", tags=["map"], summary="清空地图 + pose 归零")
async def map_reset():
    if minimap is None:
        raise HTTPException(503, "minimap not initialized")
    minimap.reset()
    push_log("minimap reset", "sys")
    return {"ok": True}


@app.get("/api/rec/status", tags=["rec"], summary="录制 / 回放状态")
async def rec_status():
    if recorder is None: raise HTTPException(503, "recorder not initialized")
    return recorder.status()


@app.post("/api/rec/start", tags=["rec"], summary="开始录制 - 清空已有事件",
          description="录制 cmd + hold_down/hold_up + 10Hz 传感器快照(ultra/pose/speed)。仅内存,服务重启清空。10 分钟上限。")
async def rec_start():
    if recorder is None: raise HTTPException(503, "recorder not initialized")
    ok, err = recorder.start()
    if not ok: raise HTTPException(409, err)
    return {"ok": True}


@app.post("/api/rec/stop", tags=["rec"], summary="停止录制")
async def rec_stop():
    if recorder is None: raise HTTPException(503, "recorder not initialized")
    ok, err = recorder.stop_recording()
    if not ok: raise HTTPException(409, err)
    return {"ok": True, "status": recorder.status()}


@app.post("/api/rec/clear", tags=["rec"], summary="清空录制(仅在 idle 时)")
async def rec_clear():
    if recorder is None: raise HTTPException(503, "recorder not initialized")
    ok, err = recorder.clear()
    if not ok: raise HTTPException(409, err)
    return {"ok": True}


@app.post("/api/rec/play", tags=["rec"], summary="开始回放",
          description=("query params:\n"
                       "- `direction`: `forward`(默认)/`reverse`\n"
                       "- `speed`: 0.1 - 5.0 倍速\n"
                       "- `calibrate`: 1 启用校准 visualization(回放时推送录制 vs 实时 ultra 偏差)\n"))
async def rec_play(direction: str = "forward", speed: float = 1.0, calibrate: int = 0):
    if recorder is None: raise HTTPException(503, "recorder not initialized")
    if direction not in ("forward", "reverse"):
        raise HTTPException(400, "direction must be forward or reverse")
    ok, err = recorder.play(direction=direction, speed=speed, calibrate=bool(calibrate))
    if not ok: raise HTTPException(409, err)
    return {"ok": True}


@app.post("/api/rec/stop_playback", tags=["rec"], summary="终止回放")
async def rec_stop_playback():
    if recorder is None: raise HTTPException(503, "recorder not initialized")
    ok, err = recorder.stop_playback()
    if not ok: raise HTTPException(409, err)
    return {"ok": True}


@app.post("/api/map/config", tags=["map"], summary="标定速度模型常数",
          description="body: `{k_linear: 0.05, k_angular: 0.0006}`  —  mm/s 和 rad/s 每单位 BUTTONSPEED")
async def map_config(req: Request):
    if minimap is None:
        raise HTTPException(503, "minimap not initialized")
    body = await req.json()
    return minimap.configure(**{k: body[k] for k in ("k_linear","k_angular") if k in body})


@app.get("/api/yolo/status", tags=["yolo"], summary="YOLO 状态 + 配置 + 最新检测",
         description="返回 enabled/loaded、当前配置、最近一次推理结果、类别列表。前端轮询或用 WS yolo 推送")
async def yolo_status():
    if yolo is None:
        return {"available": _yolo_ok, "loaded": False, "enabled": False,
                "load_error": "not initialized" if _yolo_ok else "ultralytics missing"}
    return yolo.get_state()


@app.post("/api/yolo/config", tags=["yolo"], summary="修改 YOLO 配置(enabled/conf/iou/imgsz/classes/min_interval)",
          description=("body 任意字段(都可选):\n"
                       "- `enabled` (bool): 开/关推理\n"
                       "- `conf` (0-1): 置信度阈值\n"
                       "- `iou` (0-1): NMS IoU 阈值\n"
                       "- `imgsz` (int, 自动对齐 32 倍数): 推理输入尺寸\n"
                       "- `classes` (list[int] | null): 类别 id 过滤 (null=全部)\n"
                       "- `min_interval` (float): 推理之间最小间隔秒"))
async def yolo_config(req: Request):
    if yolo is None:
        raise HTTPException(503, "yolo not initialized")
    body = await req.json()
    return yolo.configure(**{k: body[k] for k in
                             ("enabled","conf","iou","imgsz","classes","min_interval") if k in body})


@app.get("/api/yolo/models", tags=["yolo"], summary="列出可用 YOLO 模型(.pt / .onnx 文件)")
async def yolo_models():
    items = []
    try:
        for fn in os.listdir(YOLO_MODELS_DIR):
            if fn.endswith((".pt", ".onnx")):
                full = os.path.join(YOLO_MODELS_DIR, fn)
                items.append({"name": fn, "path": full,
                              "size": os.path.getsize(full)})
    except Exception:
        pass
    return {"items": items, "current": (yolo.model_path if yolo else None)}


@app.post("/api/audio/rec/start", tags=["audio"], summary="开始录音(写 wav 到 ~/car_project/records/web_<timestamp>.wav)")
async def audio_rec_start():
    if audio is None:
        raise HTTPException(503, "audio not ready")
    if state["recording"]:
        return {"ok": False, "reason": "already recording", "path": state["rec_path"]}
    fn = f"web_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
    path = os.path.join(REC_DIR, fn)
    try:
        audio.start_recording(save_path=path)
    except Exception as e:
        raise HTTPException(500, str(e))
    state["recording"] = True
    state["rec_path"] = path
    state["rec_start_t"] = time.time()
    push_log(f"rec start: {fn}", "audio")
    bcast({"type": "state", "data": state})
    return {"ok": True, "path": path}


@app.post("/api/audio/rec/stop", tags=["audio"], summary="停止当前录音并落盘")
async def audio_rec_stop():
    if audio is None:
        raise HTTPException(503, "audio not ready")
    if not state["recording"]:
        return {"ok": False, "reason": "not recording"}
    try:
        path = audio.stop_recording()
    except Exception as e:
        raise HTTPException(500, str(e))
    state["recording"] = False
    state["rec_path"] = None
    state["rec_start_t"] = None
    push_log(f"rec save: {os.path.basename(path) if path else '?'}", "audio")
    bcast({"type": "state", "data": state})
    return {"ok": True, "path": path}


@app.get("/api/recordings", tags=["audio"], summary="列出最近 30 条录音(按 mtime 降序)")
async def list_recordings():
    if not os.path.isdir(REC_DIR):
        return {"items": []}
    items = []
    for fn in os.listdir(REC_DIR):
        if not fn.endswith(".wav"): continue
        full = os.path.join(REC_DIR, fn)
        try: st = os.stat(full)
        except OSError: continue
        items.append({"name": fn, "size": st.st_size, "mtime": int(st.st_mtime)})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"items": items[:30]}


@app.post("/api/audio/play", tags=["audio"], summary="播放指定音频(支持 wav/mp3/ogg/flac/m4a)",
          description="body: `{name: \"xxx\", src: \"recordings\"|\"sounds\"|\"uploads\"}`。所有格式走 ffmpeg → ALSA。")
async def audio_play(req: Request):
    if audio is None:
        raise HTTPException(503, "audio not ready")
    body = await req.json()
    name = body.get("name", "")
    src = body.get("src", "recordings")
    if "/" in name or ".." in name or not name:
        raise HTTPException(400, "bad name")
    dirs = {"recordings": REC_DIR, "sounds": SOUND_DIR, "uploads": UPLOADS_DIR}
    base = dirs.get(src)
    if base is None:
        raise HTTPException(400, "bad src")
    full = os.path.join(base, name)
    if not os.path.exists(full):
        raise HTTPException(404, "not found")
    import subprocess as _sp
    _audio_preflight()  # 减少 USB 总电流防过流
    try:
        # 用 ffmpeg 直接输出 ALSA,任何格式都通(wav/mp3/ogg/flac/m4a/aac),
        # 进程句柄记到 audio.play_process 让 stop_playback 能 kill 它
        with audio.play_lock:
            audio._stop_playback_locked()
            audio.play_process = _sp.Popen(
                ["ffmpeg", "-loglevel", "quiet", "-nostdin",
                 "-i", full, "-f", "alsa", audio.audio_device],
                stdin=_sp.DEVNULL,
            )
    except Exception as e:
        raise HTTPException(500, str(e))
    push_log(f"play: {src}/{name}", "audio")
    return {"ok": True}


def _amixer_get(control: str, card: str = "Device") -> int | None:
    """Return mixer control as percent 0-100, or None if not found."""
    try:
        import subprocess, re
        r = subprocess.run(
            ["amixer", "-c", card, "sget", control],
            capture_output=True, text=True, timeout=2,
        )
        m = re.search(r"\[(\d+)%\]", r.stdout)
        if m: return int(m.group(1))
    except Exception:
        pass
    return None


def _amixer_set(control: str, percent: int, card: str = "Device") -> bool:
    percent = max(0, min(100, int(percent)))
    try:
        import subprocess
        subprocess.run(
            ["amixer", "-c", card, "sset", control, f"{percent}%"],
            capture_output=True, timeout=2, check=True,
        )
        return True
    except Exception:
        return False


@app.get("/api/audio/volume", tags=["audio"], summary="读 ALSA mixer 音量 (USB Audio Device)",
         description="返回 speaker / mic 当前百分比。amixer 控制项:Speaker, Mic, Auto Gain Control")
async def get_volume():
    return {
        "speaker": _amixer_get("Speaker"),
        "mic": _amixer_get("Mic"),
        "agc": _amixer_get("Auto Gain Control"),
    }


@app.post("/api/audio/volume", tags=["audio"], summary="设 ALSA mixer 音量",
          description="body 任选字段:`{speaker: 0-100, mic: 0-100, agc: 0|1}`。会通过 WS 广播,多客户端同步。")
async def set_volume(req: Request):
    body = await req.json()
    out = {}
    for k_in, ctrl in (("speaker", "Speaker"), ("mic", "Mic"), ("agc", "Auto Gain Control")):
        if k_in in body:
            ok = _amixer_set(ctrl, body[k_in])
            out[k_in] = _amixer_get(ctrl) if ok else None
            push_log(f"vol {k_in} → {out[k_in]}%", "audio")
    # 广播给所有客户端同步 slider 位置
    bcast({"type": "volume", "data": {
        "speaker": _amixer_get("Speaker"),
        "mic": _amixer_get("Mic"),
    }})
    return out


@app.post("/api/audio/upload", tags=["audio"], summary="上传 mp3/wav/ogg/flac 等音频到 ~/car_project/uploads/",
          description="multipart/form-data。允许扩展名: mp3/wav/ogg/flac/m4a/aac。最大 30MB。")
async def audio_upload(file: UploadFile = File(...)):
    name = file.filename or ""
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_AUDIO_EXT:
        raise HTTPException(400, f"unsupported ext {ext}; allowed: {sorted(ALLOWED_AUDIO_EXT)}")
    safe = os.path.basename(name)
    if "/" in safe or ".." in safe or not safe:
        raise HTTPException(400, "bad filename")
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    dest = os.path.join(UPLOADS_DIR, safe)
    size = 0
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk: break
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    f.close()
                    try: os.unlink(dest)
                    except Exception: pass
                    raise HTTPException(413, f"file too large (>{MAX_UPLOAD_SIZE//1024//1024}MB)")
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    push_log(f"upload: {safe} ({size//1024} KB)", "audio")
    return {"ok": True, "name": safe, "size": size, "path": dest}


@app.get("/api/audio/uploads", tags=["audio"], summary="列出已上传的音乐")
async def list_uploads():
    if not os.path.isdir(UPLOADS_DIR):
        return {"items": []}
    items = []
    for fn in os.listdir(UPLOADS_DIR):
        if os.path.splitext(fn)[1].lower() not in ALLOWED_AUDIO_EXT: continue
        full = os.path.join(UPLOADS_DIR, fn)
        try: st = os.stat(full)
        except OSError: continue
        items.append({"name": fn, "size": st.st_size, "mtime": int(st.st_mtime)})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"items": items[:50]}


@app.delete("/api/audio/upload/{name}", tags=["audio"], summary="删除单个上传文件")
async def delete_upload(name: str):
    if "/" in name or ".." in name or not name:
        raise HTTPException(400, "bad name")
    p = os.path.join(UPLOADS_DIR, name)
    if not os.path.exists(p):
        raise HTTPException(404, "not found")
    try:
        os.unlink(p)
        push_log(f"upload deleted: {name}", "audio")
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"ok": True}


@app.delete("/api/recordings", tags=["audio"], summary="清空所有录音(只删 *.wav)")
async def clear_recordings():
    if not os.path.isdir(REC_DIR):
        return {"ok": True, "removed": 0}
    n = 0; err = 0
    for fn in os.listdir(REC_DIR):
        if not fn.endswith(".wav"): continue
        try:
            os.unlink(os.path.join(REC_DIR, fn))
            n += 1
        except Exception:
            err += 1
    push_log(f"recordings cleared: {n} files removed, {err} errors", "audio")
    return {"ok": True, "removed": n, "errors": err}


@app.post("/api/audio/stop", tags=["audio"], summary="停止当前播放 (audio_driver 和 car_driver 两路 aplay 都 kill)")
async def audio_stop():
    import subprocess as _sp
    sources = []
    # 1) AudioController 的播放
    if audio is not None:
        try:
            audio.stop_playback()
            sources.append("audio")
        except Exception as e:
            push_log(f"audio_driver stop err: {e}", "sys")
    # 2) CarController 的猫叫播放(cat1-4 走的是 car_driver.play_wav,跟 audio_driver 是不同 subprocess)
    if car is not None:
        try:
            with car.audio_lock:
                if car.audio_process is not None and car.audio_process.poll() is None:
                    car.audio_process.terminate()
                    try: car.audio_process.wait(timeout=0.3)
                    except _sp.TimeoutExpired: car.audio_process.kill()
                    car.audio_process = None
                    sources.append("car")
        except Exception as e:
            push_log(f"car audio stop err: {e}", "sys")
    push_log(f"audio stopped ({', '.join(sources) or 'nothing playing'})", "audio")
    return {"ok": True, "stopped_sources": sources}


# ====================== Camera stream ======================

def _get_cam(cid: str | None = None):
    """cid=None 走 main_cam alias。"""
    cams = getattr(app.state, "cameras", {}) or {}
    if cid is None: cid = getattr(app.state, "main_cam_id", None)
    return cams.get(cid) if cid else None


async def mjpeg_generator(cid: str | None = None):
    boundary = b"--frame\r\n"
    last_t = 0.0
    while True:
        cam = _get_cam(cid)
        if cam is None:
            await asyncio.sleep(0.5)
            continue
        jpeg, t = cam.get_jpeg()
        if jpeg is None or t == last_t:
            await asyncio.sleep(0.03)
            continue
        last_t = t
        chunk = (boundary
                 + b"Content-Type: image/jpeg\r\n"
                 + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                 + jpeg + b"\r\n")
        yield chunk


@app.get("/api/camera/stream.mjpg", tags=["camera"], summary="MJPEG 主摄像头流 (兼容)",
         description="multipart/x-mixed-replace 推送主 cam 的实时 JPEG。指定具体 cam 用 `/api/camera/{cam_id}/stream.mjpg`(cam_id 为 a/b/c...)。")
async def camera_stream():
    if _get_cam() is None: raise HTTPException(503, "main camera not ready")
    return StreamingResponse(
        mjpeg_generator(None),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                 "Pragma": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/camera/{cam_id}/stream.mjpg", tags=["camera"], summary="MJPEG 指定 cam 流")
async def camera_stream_byid(cam_id: str):
    if _get_cam(cam_id) is None: raise HTTPException(503, f"camera {cam_id} not ready")
    return StreamingResponse(
        mjpeg_generator(cam_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                 "Pragma": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/camera/snapshot.jpg", tags=["camera"], summary="主 cam 当前帧 JPEG (兼容)")
async def camera_snapshot():
    cam = _get_cam()
    if cam is None: raise HTTPException(503, "main camera not ready")
    jpeg, _ = cam.get_jpeg()
    if not jpeg: raise HTTPException(503, "no frame yet")
    return StreamingResponse(iter([jpeg]), media_type="image/jpeg")


@app.get("/api/camera/{cam_id}/snapshot.jpg", tags=["camera"], summary="指定 cam 当前帧 JPEG")
async def camera_snapshot_byid(cam_id: str):
    cam = _get_cam(cam_id)
    if cam is None: raise HTTPException(503, f"camera {cam_id} not ready")
    jpeg, _ = cam.get_jpeg()
    if not jpeg: raise HTTPException(503, "no frame yet")
    return StreamingResponse(iter([jpeg]), media_type="image/jpeg")


@app.get("/api/camera/list", tags=["camera"], summary="所有 cam 列表 + 状态")
async def camera_list():
    cams = getattr(app.state, "cameras", {}) or {}
    main = getattr(app.state, "main_cam_id", None)
    out = []
    for cid, cs in cams.items():
        out.append({
            "id": cid, "is_main": (cid == main),
            "ok": (cs is not None and cs.is_alive() if cs else False),
            "device": (cs.device if cs else None),
            "fps": round(cs.fps(), 1) if cs else 0.0,
            "frame_age_ms": int((time.time() - cs.last_frame_t) * 1000) if (cs and cs.last_frame_t) else None,
            "flipped": bool(getattr(cs, "flipped", False)) if cs else False,
            "error": (cs.error if cs else "not initialized"),
        })
    return {"cameras": out, "main_id": main}


CAM_STATE_FILE = "/home/pi/.config/robot-console/cam_state.json"

def _save_cam_state():
    """持久化 main_id + 每个 cam 的 flipped 状态。"""
    cams = getattr(app.state, "cameras", {}) or {}
    state_obj = {
        "main_id": getattr(app.state, "main_cam_id", None),
        "cams": {cid: {"flipped": bool(getattr(cs, "flipped", False))} for cid, cs in cams.items() if cs},
    }
    try:
        os.makedirs(os.path.dirname(CAM_STATE_FILE), exist_ok=True)
        with open(CAM_STATE_FILE, "w") as f:
            json.dump(state_obj, f, indent=2)
    except Exception as e:
        push_log(f"cam_state save failed: {e}", "sys")

def _load_cam_state():
    """启动时 apply state 到当前 cam streamer。"""
    if not os.path.exists(CAM_STATE_FILE): return
    try:
        with open(CAM_STATE_FILE) as f: state_obj = json.load(f)
    except Exception: return
    cams = getattr(app.state, "cameras", {}) or {}
    for cid, info in (state_obj.get("cams") or {}).items():
        cs = cams.get(cid)
        if cs and isinstance(info, dict):
            cs.flipped = bool(info.get("flipped", False))
    # 主 cam:仅当 saved id 仍 alive 时切
    saved_main = state_obj.get("main_id")
    if saved_main and cams.get(saved_main):
        app.state.main_cam_id = saved_main
        global camera
        camera = cams[saved_main]


@app.post("/api/camera/config", tags=["camera"], summary="设置主 cam / 翻转",
          description='body 任意字段:\n- `main_id`: a/b/... 切主 cam\n- `cam_id` + `flipped`: 单个 cam 设置 180° 颠倒(H+V flip,服务端 cv2.flip)\n所有修改自动 persist 到 /home/pi/.config/robot-console/cam_state.json,开机重启保留。')
async def camera_config(req: Request):
    body = await req.json()
    cams = getattr(app.state, "cameras", {}) or {}
    changed = False
    if "main_id" in body:
        new_main = body["main_id"]
        if new_main not in cams:
            raise HTTPException(400, f"unknown cam id {new_main}; available: {list(cams.keys())}")
        if cams.get(new_main) is None:
            raise HTTPException(400, f"cam {new_main} not alive")
        app.state.main_cam_id = new_main
        global camera
        camera = cams[new_main]
        push_log(f"main cam → {new_main}", "sys")
        changed = True
    if "cam_id" in body and "flipped" in body:
        cid = body["cam_id"]
        cs = cams.get(cid)
        if cs is None:
            raise HTTPException(400, f"cam {cid} not available")
        cs.flipped = bool(body["flipped"])
        push_log(f"cam_{cid} flipped={cs.flipped}", "sys")
        changed = True
    if changed: _save_cam_state()
    return {"ok": True, "main_id": getattr(app.state, "main_cam_id", None),
            "cams": {cid: {"flipped": getattr(cs, "flipped", False)} for cid, cs in cams.items() if cs}}


# ====================== WebSocket ======================

@app.websocket("/ws")
# WebSocket - 不出现在 OpenAPI,文档见 API.md 与 root description
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    try:
        await websocket.send_text(json.dumps({
            "type": "snapshot",
            "data": {"state": state, "logs": list(log_buf)[-80:]},
        }, ensure_ascii=False, default=str))
        while True:
            msg = await websocket.receive_text()
            try:
                m = json.loads(msg)
                t = m.get("type")
                if t == "ping":
                    await websocket.send_text(json.dumps({"type": "pong", "t": now_ms()}))
                elif t == "hold" and holder is not None:
                    # 后端管 hold thread,前端只发 down/renew/up 边沿
                    st = m.get("state")
                    act = m.get("action")
                    if st == "down" and act:
                        holder.hold(act)
                        if recorder is not None: recorder.log_event("hold_down", {"action": act})
                    elif st == "renew":
                        holder.renew(act)
                        # renew 不录(高频且对回放无信息量,playback 内部会自行 renew)
                    elif st == "up":
                        # 带 act = 单键释放(剩余 action 继续 RR);不带 = 全清(兜底)
                        holder.release(act)
                        if recorder is not None: recorder.log_event("hold_up", {"action": act})
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        clients.discard(websocket)
        # WS 断开 = 该客户端不再 hold,主动 release 防止 zombie
        if holder is not None and len(clients) == 0:
            holder.release(reason="last ws disconnect")


# ====================== HTML ======================

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>ROBOT CONTROL UNIT // RPI-01</title>
<link rel="preconnect" href="https://fonts.loli.net">
<link rel="preconnect" href="https://gstatic.loli.net" crossorigin>
<link href="https://fonts.loli.net/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  /* 全部统一用 JetBrains Mono(失败 fallback 到系统等宽) */
  :root{
    --font-mono: 'JetBrains Mono', ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, 'Liberation Mono', 'Courier New', monospace;
    --font-display: 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, Consolas, 'Courier New', monospace;
    --font-tech: 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, Consolas, 'Courier New', monospace;
  }
</style>
<style>
:root{
  /* 玄云·tmux 化绿调 */
  --bg:#0d130f; --bg2:#0f1812; --panel:#131f17; --panel2:#192a1f;
  --line:#1f3527; --line2:#2e4d38;
  /* --amber 是 legacy 名,实际是 mint 绿主调 — 大量样式按这个名字引用,改 token 全局生效 */
  --amber:#6fd685; --amber-d:#4ea866; --amber-l:#a3e6b0;
  --red:#ff8585; --red-d:#a04545;
  --green:#6fd685; --cyan:#7fe8c6;
  --warn:#f4c46b;
  --text:#cfe8d4; --text-dim:#7a9485; --text-mute:#4d6056;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--text);
  font-family:var(--font-mono);user-select:none;-webkit-user-select:none;
}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:1;
  background:repeating-linear-gradient(0deg,rgba(111,214,133,.025) 0,rgba(111,214,133,.025) 1px,transparent 1px,transparent 3px),
    radial-gradient(ellipse at 50% -20%,#1a140a 0%,#0b0907 70%);
}
body::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:2;
  background:radial-gradient(ellipse at center,transparent 40%,rgba(0,0,0,.55) 100%);
  mix-blend-mode:multiply;
}

#app{position:relative;z-index:5;height:100vh;display:grid;
  grid-template-rows:54px 1fr 220px;
  grid-template-columns:1.4fr 1fr 1fr 320px;
  grid-template-areas:
    "head head head head"
    "cam  tele deck side"
    "log  log  log  side";
  gap:1px;padding:8px;background:var(--bg2);
}
/* OPERATE: 主 cam 大,tele+deck 右,side 收起,log 缩为 28px collapse */
body.mode-operate #app{
  grid-template-rows:54px 1fr 32px;
  grid-template-columns:1.7fr 1fr 1fr;
  grid-template-areas:
    "head head head"
    "cam  tele deck"
    "log  log  log";
}
body.mode-operate .pane-side{display:none}
body.mode-operate .pane-log .panel-body{display:none}
body.mode-operate .pane-log .panel-head{cursor:pointer}
/* DEBUG: 现有四列全展示 */
body.mode-debug{}
/* SETTINGS: 隐藏 deck (主操作面板),保留 cam (调 layout) + side (audio 音量) + tele (校准),
   主区第二列改用 settings 占位(用现有 tele panel 内的内容 + cam config + audio) */
body.mode-settings #app{
  grid-template-rows:54px 1fr 32px;
  grid-template-columns:1.4fr 1fr 320px;
  grid-template-areas:
    "head head head"
    "cam  tele side"
    "log  log  log";
}
body.mode-settings .pane-deck{display:none}
body.mode-settings .pane-log .panel-body{display:none}
body.mode-settings .pane-log .panel-head{cursor:pointer}

.panel{background:var(--bg2);border:1px solid var(--line);position:relative;overflow:hidden;display:flex;flex-direction:column}
.panel-head{flex:0 0 28px;display:flex;align-items:center;padding:0 12px;
  background:linear-gradient(180deg,#1f1a12,#15110b);border-bottom:1px solid var(--line);
  font-family:var(--font-display);font-size:10.5px;letter-spacing:.22em;color:var(--amber);text-transform:uppercase;
}
.panel-head::before{content:"▸";margin-right:8px}
.panel-head .ph-r{margin-left:auto;color:var(--text-dim);font-size:9px;letter-spacing:.2em}
.tab-btn{margin-left:8px;background:transparent;border:none;color:var(--text-mute);
  font-family:var(--font-mono);font-size:10px;letter-spacing:.18em;
  padding:2px 6px;cursor:pointer;text-transform:uppercase;border-bottom:2px solid transparent}
.tab-btn.active{color:var(--amber);border-bottom-color:var(--amber)}
.tab-btn:hover:not(.active){color:var(--amber-l)}
.tab-content{display:flex;flex-direction:column;flex:1;gap:12px;min-height:0}
.map-reset-btn{margin-left:10px;background:transparent;border:1px solid var(--line2);
  color:var(--text-dim);font-family:var(--font-mono);font-size:9px;letter-spacing:.15em;
  padding:2px 8px;cursor:pointer;text-transform:uppercase;height:18px}
.map-reset-btn:hover{border-color:var(--amber);color:var(--amber);background:rgba(111,214,133,.06)}

/* Recording panel */
.rec-progress-row{display:flex;align-items:center;gap:8px}
.rec-progress-track{flex:1;height:4px;background:#1a1610;border:1px solid var(--line2);position:relative;overflow:hidden}
.rec-progress-fill{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,#6fd685,#4ea866);width:0%;transition:width .15s}
.rec-progress-meta{font-family:var(--font-mono);font-size:10px;color:var(--text-dim);letter-spacing:.1em;min-width:90px;text-align:right}
.rec-current{font-family:var(--font-mono);font-size:11px;color:var(--amber);letter-spacing:.18em;padding:4px 8px;
  background:rgba(111,214,133,.07);border-left:2px solid var(--amber);text-transform:uppercase}
.cal-toggle{display:flex;align-items:center;gap:8px;margin-top:8px;font-family:var(--font-mono);
  font-size:10px;color:var(--text-dim);letter-spacing:.15em;text-transform:uppercase;cursor:pointer}
.cal-toggle input{margin:0;accent-color:var(--amber)}
.rec-cal-diff{margin-top:6px;font-family:var(--font-mono);font-size:10px}
.rec-cal-row{display:grid;grid-template-columns:40px 1fr 1fr 1fr;gap:6px;padding:2px 0;color:var(--text-dim);align-items:center}
.rec-cal-row>span:first-child{color:var(--amber);letter-spacing:.1em}
.rec-cal-row>span:nth-child(2){color:#5af0ff}
.rec-cal-row>span:nth-child(3){color:#7fff5a}
.rec-cal-row>span:last-child{color:var(--text-bright);text-align:right;font-variant-numeric:tabular-nums}
/* Arm toolbar */
.arm-toolbar{display:flex;gap:4px;align-items:center}
/* Arm preset slots */
.arm-preset-row{display:grid;grid-template-columns:repeat(5,1fr);gap:4px;margin-top:6px}
.arm-preset-cell{display:grid;grid-template-rows:1fr 18px;gap:2px}
.arm-preset-cell .preset-move{padding:8px 2px;font-size:10px;letter-spacing:.05em;line-height:1.1}
.arm-preset-cell .preset-move.on{background:rgba(111,214,133,.16);border-color:var(--amber);color:var(--amber)}
.arm-preset-cell .preset-move.cur{background:rgba(111,214,133,.4);border-color:var(--amber);color:var(--bg);box-shadow:inset 0 0 8px rgba(111,214,133,.4)}
.arm-preset-cell .preset-save{padding:2px 0;font-size:11px;border-color:var(--line2);color:var(--text-dim)}
.arm-preset-cell .preset-save:hover{color:var(--amber);border-color:var(--amber)}
#btnArmLoopStart.on{background:rgba(244,196,107,.2);border-color:var(--warn);color:var(--warn)}
.arm-toolbar .btn{flex:1;padding:6px 4px;font-size:10px;letter-spacing:.05em}
.arm-toolbar .badge{margin-left:4px}
/* Arm joint row */
.arm-joint-row{padding:4px 6px;background:#1a1610;border:1px solid var(--line);border-radius:1px}
.arm-joint-row.uncalibrated{border-color:#ff7a6e;background:rgba(255,58,46,.04)}
.arm-joint-row.uncalibrated .arm-joint-limit{color:#ff7a6e}
.arm-joint-head{display:flex;align-items:baseline;gap:6px;font-family:var(--font-mono);font-size:10px;margin-bottom:3px}
.arm-joint-name{color:var(--text-dim);letter-spacing:.15em;font-weight:600;min-width:28px}
.arm-joint-desc{flex:1;color:var(--amber);font-size:10px;letter-spacing:.08em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.arm-joint-limit{color:var(--text-mute);font-size:9px;font-family:var(--font-tech);letter-spacing:.05em}
.arm-obs{color:#7fff5a;font-size:9px;font-family:var(--font-tech);letter-spacing:.05em;margin-left:6px;border-left:1px solid var(--line2);padding-left:6px}
.arm-joint-ctrl{display:grid;grid-template-columns:54px 1fr 24px 24px;gap:4px;align-items:center;font-family:var(--font-mono);font-size:10px}
.arm-joint-val{color:var(--amber);text-align:right;font-family:var(--font-tech);font-size:11px}
.arm-joint-input{width:100%;background:#1a1610;border:1px solid var(--line2);color:var(--cyan);font-family:var(--font-mono);font-size:11px;padding:2px 4px;text-align:right;transition:border-color .15s,background .15s,color .15s}
.arm-joint-input:focus{outline:none;border-color:var(--amber)}
.arm-joint-input:disabled{opacity:0.4;cursor:not-allowed;background:#0e0a06}
.arm-nudge{padding:2px 0;font-size:11px;letter-spacing:0;transition:background .15s,border-color .15s,color .15s}
.arm-nudge-pending{background:rgba(111,214,133,.25)!important;border-color:var(--amber)!important;color:var(--amber)!important}
.arm-nudge-ok{background:rgba(127,255,90,.3)!important;border-color:#7fff5a!important;color:#7fff5a!important}
.arm-nudge-err{background:rgba(255,58,46,.3)!important;border-color:#ff3a2e!important;color:#ff7a6e!important}

#btnRecToggle.recording{background:rgba(255,58,46,.15);border-color:#ff3a2e;color:#ff7a6e}
#btnRecToggle.recording::before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;background:#ff3a2e;margin-right:6px;animation:pulse 1s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.panel-body{padding:12px;flex:1;overflow:hidden;position:relative;display:flex;flex-direction:column;gap:10px}

header{grid-area:head;background:linear-gradient(90deg,#1a1410,#15110b 50%,#1a1410);
  border:1px solid var(--line);display:flex;align-items:center;padding:0 22px;position:relative;gap:14px;
}
header::after{content:"";position:absolute;bottom:-1px;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,var(--amber),transparent);opacity:.7;
}
/* 顶部 mode 切换 nav */
.mode-nav{display:flex;gap:2px;margin-left:14px}
.mode-btn{background:transparent;border:1px solid var(--line2);color:var(--text-dim);
  font-family:var(--font-mono);font-size:10px;letter-spacing:.22em;padding:6px 14px;
  cursor:pointer;text-transform:uppercase;transition:all .15s;height:28px}
.mode-btn:hover{color:var(--amber-l);border-color:var(--amber);background:rgba(111,214,133,.06)}
.mode-btn.active{background:rgba(111,214,133,.18);border-color:var(--amber);color:var(--amber);box-shadow:inset 0 0 12px rgba(111,214,133,.18)}
.h-brand{font-family:var(--font-display);font-size:20px;letter-spacing:.32em;color:var(--amber);
  text-transform:uppercase;text-shadow:0 0 12px rgba(111,214,133,.4)}
.h-sub{font-size:10px;color:var(--text-dim);letter-spacing:.22em;text-transform:uppercase}
.h-right{margin-left:auto;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.h-stat{display:flex;align-items:center;gap:8px;font-size:10.5px;color:var(--text-dim);letter-spacing:.12em;text-transform:uppercase;cursor:default}
.h-stat[title]{cursor:help}
.dot{width:8px;height:8px;border-radius:50%;background:var(--text-mute);box-shadow:0 0 6px rgba(111,214,133,.4);transition:background .2s,box-shadow .2s}
.dot.ok{background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 2s infinite}
.dot.warn{background:var(--amber);box-shadow:0 0 8px var(--amber);animation:pulse 1.2s infinite}
.dot.error{background:var(--red);box-shadow:0 0 8px var(--red);animation:pulse .6s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
@keyframes flicker{0%,100%{opacity:1}50%{opacity:.55}}
@keyframes scan{0%{transform:translateY(-100%)}100%{transform:translateY(100%)}}

/* ============ CAM PANEL ============ */
.pane-cam{grid-area:cam}
.pane-cam .panel-body{gap:10px;padding:10px}

.cam-frame{position:relative;width:100%;aspect-ratio:4/3;background:#000;border:1px solid var(--line2);overflow:hidden;flex:0 0 auto;
  max-height:55vh}
.pane-cam .panel-body{overflow-y:auto}
.cam-frame::before{content:"";position:absolute;inset:0;
  background:repeating-linear-gradient(0deg,transparent 0,transparent 2px,rgba(111,214,133,.04) 2px,rgba(111,214,133,.04) 3px);
  pointer-events:none;z-index:2;
}
.cam-frame::after{content:"";position:absolute;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(111,214,133,.4),transparent);
  animation:scan 3.5s linear infinite;pointer-events:none;z-index:3;
}
.cam-hud{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:4}
.cam-hud rect.hud-bar.hot{animation:flicker .35s infinite}
/* REAR CAM 镜像:cam-img 水平翻转;bbox 由 JS 反转 x 坐标(保留文字方向);HUD/corner 不动 */
.cam-frame.mirror .cam-img{transform:scaleX(-1)}
.cam-img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;background:#000;display:block;z-index:1}

/* 多 cam stack layout */
.cam-stack{display:flex;gap:6px;flex:0 0 auto}
.cam-stack.layout-side{flex-direction:row}
.cam-stack.layout-side .cam-frame{flex:1 1 50%;aspect-ratio:4/3}
.cam-stack.layout-stack{flex-direction:column}
.cam-stack.layout-pip{display:block;position:relative}
.cam-stack.layout-pip .cam-frame.primary{width:100%}
.cam-stack.layout-pip .cam-frame.secondary{position:absolute;right:8px;bottom:8px;width:28%;aspect-ratio:4/3;
  border:2px solid var(--amber);box-shadow:0 4px 12px rgba(0,0,0,.6);z-index:3;cursor:pointer}
.cam-stack.layout-single .cam-frame.secondary{display:none}
.cam-stack.layout-single .cam-frame.primary{width:100%}
/* 副 cam 标签 */
.cam-id-tag{position:absolute;top:4px;left:6px;font-family:var(--font-tech);font-size:9px;
  letter-spacing:.2em;color:var(--amber);background:rgba(0,0,0,.55);padding:2px 6px;z-index:4;
  border:1px solid rgba(111,214,133,.3)}
.cam-flip-btn{position:absolute;top:4px;right:6px;width:22px;height:22px;
  font-family:var(--font-tech);font-size:14px;line-height:18px;
  background:rgba(0,0,0,.6);color:var(--text-dim);border:1px solid var(--line2);
  cursor:pointer;z-index:5;padding:0;text-align:center}
.cam-flip-btn:hover{color:var(--amber);border-color:var(--amber);background:rgba(111,214,133,.18)}
.cam-flip-btn.on{color:var(--amber);border-color:var(--amber);background:rgba(111,214,133,.28);
  box-shadow:inset 0 0 6px rgba(111,214,133,.4)}
.cam-frame.primary .cam-id-tag{border-color:var(--amber);background:rgba(111,214,133,.15)}
/* layout / main 配置控件 */
.cam-cfg-row{display:flex;gap:6px;align-items:center;font-family:var(--font-mono);font-size:10px;color:var(--text-dim);letter-spacing:.1em}
.cam-cfg-row select{background:#1a1610;border:1px solid var(--line2);color:var(--amber);
  font-family:var(--font-mono);font-size:10px;letter-spacing:.1em;padding:2px 6px;cursor:pointer;text-transform:uppercase}
.cam-cfg-row select:focus{border-color:var(--amber);outline:none}
.cam-corner{position:absolute;width:14px;height:14px;border:1.5px solid var(--amber);z-index:4;opacity:.85}
.cam-corner.tl{top:4px;left:4px;border-right:none;border-bottom:none}
.cam-corner.tr{top:4px;right:4px;border-left:none;border-bottom:none}
.cam-corner.bl{bottom:4px;left:4px;border-right:none;border-top:none}
.cam-corner.br{bottom:4px;right:4px;border-left:none;border-top:none}
.cam-meta{position:absolute;left:6px;bottom:6px;font-family:var(--font-tech);font-size:10px;color:var(--amber);text-shadow:0 0 6px rgba(0,0,0,.8);z-index:4}
.cam-rec{position:absolute;right:6px;top:6px;font-family:var(--font-tech);font-size:10px;color:var(--red);
  display:flex;align-items:center;gap:6px;z-index:4;text-shadow:0 0 6px rgba(0,0,0,.8)}
.cam-rec .recdot{width:8px;height:8px;border-radius:50%;background:var(--red);box-shadow:0 0 8px var(--red);animation:pulse 1.2s infinite}
.cam-overlay-err{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--red);
  font-family:var(--font-display);font-size:13px;letter-spacing:.3em;z-index:5;background:rgba(0,0,0,.7);
  text-shadow:0 0 8px var(--red)}

.filter-row{display:grid;grid-template-columns:50px 1fr;gap:8px;align-items:center;margin-top:6px}
.filter-row input[type="text"]{background:var(--panel);border:1px solid var(--line2);color:var(--text);
  font-family:var(--font-mono);font-size:10px;padding:5px 7px;outline:none;letter-spacing:.05em}
.filter-row input[type="text"]:focus{border-color:var(--amber);box-shadow:0 0 6px rgba(111,214,133,.25)}
.filter-row input[type="text"]::placeholder{color:var(--text-mute)}
.slider-row{display:grid;grid-template-columns:50px 1fr 48px;gap:8px;align-items:center;font-size:10px;margin-top:5px}
.slider-label{color:var(--text-dim);letter-spacing:.18em;text-transform:uppercase}
.slider-val{font-family:var(--font-tech);color:var(--amber);text-align:right;font-size:11px}
input[type="range"]{accent-color:var(--amber);height:4px;cursor:pointer}

.yolo-box-rect{fill:none;stroke-width:1.5}
.yolo-box-label{font-family:var(--font-tech);font-size:10px;fill:#0b0907;font-weight:700;letter-spacing:0.5px}

.sys-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-top:auto}
.sys-card{border:1px solid var(--line);padding:6px 8px;background:var(--panel);text-align:center}
.sys-card .sys-label{font-size:8px;color:var(--text-dim);letter-spacing:.15em;text-transform:uppercase}
.sys-card .sys-val{font-family:var(--font-tech);font-size:14px;color:var(--amber);line-height:1.1;margin-top:2px}
.sys-card.warn .sys-val{color:var(--red)}

/* ============ TELE ============ */
.pane-tele{grid-area:tele}
.schem-wrap{position:relative;flex:1 1 0;min-height:150px;border:1px solid var(--line);
  background:
    repeating-linear-gradient(0deg,rgba(111,214,133,.04) 0,rgba(111,214,133,.04) 1px,transparent 1px,transparent 24px),
    repeating-linear-gradient(90deg,rgba(111,214,133,.04) 0,rgba(111,214,133,.04) 1px,transparent 1px,transparent 24px),
    #0e0b07;
}
.schem-l,.schem-r{position:absolute;top:6px;font-size:9px;letter-spacing:.2em;text-transform:uppercase}
.schem-l{left:8px;color:var(--amber);opacity:.7}
.schem-r{right:8px;color:var(--text-dim)}
svg.schem{width:100%;height:100%;display:block}

.ureadings{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;flex:0 0 auto}
.ureading{border:1px solid var(--line);padding:9px 11px;background:var(--panel);position:relative}
.ureading::before{content:"";position:absolute;left:-1px;top:-1px;width:8px;height:8px;border-top:1px solid var(--amber);border-left:1px solid var(--amber)}
.ureading::after{content:"";position:absolute;right:-1px;bottom:-1px;width:8px;height:8px;border-bottom:1px solid var(--amber);border-right:1px solid var(--amber)}
.ureading-label{font-size:9px;color:var(--text-dim);letter-spacing:.2em;text-transform:uppercase}
.ureading-row{display:flex;align-items:baseline;margin-top:2px}
.ureading-val{font-family:var(--font-tech);font-size:24px;color:var(--amber);text-shadow:0 0 8px rgba(111,214,133,.4);line-height:1}
.ureading-val.danger{color:var(--red);text-shadow:0 0 10px var(--red);animation:flicker .35s infinite}
.ureading-unit{font-size:10px;color:var(--text-mute);margin-left:5px}
.ureading-bar{margin-top:6px;height:3px;background:var(--bg);border:1px solid var(--line);position:relative}
.ureading-bar > div{height:100%;background:var(--amber);transition:width .2s}
.ureading.danger .ureading-bar > div{background:var(--red)}

/* ============ DECK ============ */
.pane-deck{grid-area:deck}
.pane-deck .panel-body{overflow-y:auto}
.pane-side .panel-body{overflow-y:auto}
/* tab content 也要让内部可滚 */
.tab-content{min-height:0}
.deck-status{display:grid;grid-template-columns:1fr 1fr;gap:6px;flex:0 0 auto}
.stat-card{border:1px solid var(--line);padding:8px 10px;background:var(--panel);position:relative}
.stat-card .stat-label{font-size:9px;color:var(--text-dim);letter-spacing:.2em;text-transform:uppercase}
.stat-card .stat-val{font-family:var(--font-tech);font-size:18px;color:var(--amber);margin-top:2px;line-height:1.1;letter-spacing:.05em}
.stat-card.on .stat-val{color:var(--green)}
.stat-card.danger .stat-val{color:var(--red);animation:flicker .4s infinite}

.key-grid{display:grid;grid-template-columns:repeat(3,1fr);grid-template-rows:60px 60px;gap:5px;flex:0 0 auto}
.key{position:relative;background:linear-gradient(180deg,#2a2418,#1c1810);
  border:1px solid var(--line2);border-bottom-width:3px;color:var(--amber);
  font-family:var(--font-mono);font-weight:700;font-size:18px;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  cursor:pointer;transition:transform .05s,background .08s,box-shadow .08s,border-color .08s;
}
.key small{font-size:8px;color:var(--text-dim);font-weight:400;letter-spacing:.12em;margin-top:3px}
.key.empty{background:transparent;border:1px dashed var(--line);cursor:default}
.key:not(.empty):active,.key.active{
  background:linear-gradient(180deg,#5a4818,#6fd685);color:var(--bg);
  border-color:var(--amber);border-bottom-width:1px;transform:translateY(2px);
  box-shadow:0 0 22px rgba(111,214,133,.55),inset 0 -2px 4px rgba(0,0,0,.35);
}
.key.active small,.key:not(.empty):active small{color:rgba(0,0,0,.55)}

.rotate-row{display:grid;grid-template-columns:1fr 1fr;gap:5px;flex:0 0 auto}
.rotate-row .key{height:40px;font-size:14px}

.btn-row{display:grid;gap:5px;flex:0 0 auto}
.btn-2{grid-template-columns:1fr 1fr}.btn-3{grid-template-columns:repeat(3,1fr)}.btn-4{grid-template-columns:repeat(4,1fr)}
.btn{background:var(--panel);border:1px solid var(--line2);color:var(--text);
  font-family:var(--font-mono);font-size:10.5px;letter-spacing:.12em;
  padding:9px 10px;cursor:pointer;text-transform:uppercase;
  transition:background .1s,color .1s,border-color .1s,box-shadow .1s;
  display:flex;align-items:center;justify-content:space-between;text-align:left}
.btn:hover{background:#1f1810;border-color:var(--amber);color:var(--amber)}
.btn:active{background:var(--amber);color:var(--bg);border-color:var(--amber)}
.btn .badge{font-size:9px;color:var(--text-mute);letter-spacing:.15em}
.btn:hover .badge{color:var(--amber-l)}
.btn.on{background:rgba(111,214,133,.14);border-color:var(--amber);color:var(--amber);box-shadow:inset 0 0 14px rgba(111,214,133,.18)}
.btn.on .badge{color:var(--amber)}
.btn.compact{padding:8px 6px;font-size:10px;justify-content:center}

.btn-danger{background:linear-gradient(180deg,#2a0e0a,#1a0805);border:2px solid var(--red-d);
  color:var(--red);font-size:13px;font-weight:800;padding:12px 50px;letter-spacing:.3em;position:relative;
  flex:0 0 auto;display:flex;align-items:center;justify-content:center}
.btn-danger::before,.btn-danger::after{content:"";position:absolute;top:0;bottom:0;width:38px;
  background:repeating-linear-gradient(45deg,var(--red-d),var(--red-d) 4px,transparent 4px,transparent 8px);opacity:.55}
.btn-danger::before{left:0}.btn-danger::after{right:0}
.btn-danger:hover{background:var(--red-d);color:var(--bg);box-shadow:0 0 30px rgba(255,58,46,.5)}

/* ============ LOG ============ */
.pane-log{grid-area:log}
.pane-log .panel-body{padding:8px 12px}
.log-body{overflow-y:auto;flex:1;font-family:var(--font-tech);font-size:11.5px;line-height:1.6;color:var(--text-dim)}
.log-line{display:flex;gap:10px;padding:1px 4px;border-left:2px solid transparent}
.log-line:hover{background:rgba(111,214,133,.05);border-left-color:var(--amber)}
.log-line .ts{color:var(--text-mute);flex:0 0 88px}
.log-line .kind{flex:0 0 56px;color:var(--amber);font-size:10px;text-transform:uppercase}
.log-line .msg{color:var(--text);flex:1;word-break:break-all}
.log-line.kind-cmd .kind{color:var(--cyan)}.log-line.kind-cmd .msg{color:var(--cyan)}
.log-line.kind-audio .kind{color:var(--amber-l)}
.log-line.kind-ps3 .kind{color:var(--green)}
.log-line.kind-sys .kind{color:var(--text-dim)}.log-line.kind-sys .msg{color:var(--text-dim)}

/* ============ SIDE ============ */
.pane-side{grid-area:side}
.audio-list{overflow-y:auto;flex:1 1 0;border:1px solid var(--line);background:var(--panel);min-height:80px}
.audio-item{padding:6px 10px;font-size:10px;color:var(--text-dim);cursor:pointer;
  border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;
  font-family:var(--font-tech);letter-spacing:.05em}
.audio-item:last-child{border-bottom:none}
.audio-item:hover{color:var(--amber);background:rgba(111,214,133,.05)}
.audio-item .size{color:var(--text-mute);font-size:9px}
.audio-empty{padding:24px 12px;text-align:center;color:var(--text-mute);font-size:10px;letter-spacing:.2em}
.sec-label{font-size:9px;letter-spacing:.22em;color:var(--text-dim);text-transform:uppercase;margin-bottom:6px}

::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--line2)}
::-webkit-scrollbar-thumb:hover{background:var(--amber-d)}

@media (max-width:1200px){
  #app{grid-template-columns:340px 1fr 320px;
    grid-template-areas:
      "head head head"
      "cam  deck side"
      "tele deck side"
      "log  log  log";
    grid-template-rows:54px auto auto 200px}
}
@media (max-width:820px){
  #app{grid-template-rows:54px auto auto auto auto auto;
    grid-template-columns:1fr;
    grid-template-areas:"head" "cam" "tele" "deck" "side" "log"}
  .pane-log{height:220px}
}
</style>
</head>
<body>
<div id="deadlockBanner" style="display:none;position:fixed;top:0;left:0;right:0;
  background:linear-gradient(90deg,#a8221a,#ff3a2e,#a8221a);color:#0b0907;
  text-align:center;padding:12px 20px;font-weight:800;letter-spacing:.2em;z-index:9999;
  font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;font-size:11.5px;
  border-bottom:2px solid #0b0907;text-transform:uppercase;line-height:1.5">
  ⚠ ESP32 FIRMWARE DEADLOCK · auto-recovery exhausted<br>
  <span style="font-weight:500;letter-spacing:.12em">
    try
    <button id="btnTryRtsReset" style="background:#0b0907;color:#ff3a2e;border:1px solid #0b0907;padding:3px 10px;font-family:inherit;font-size:11px;font-weight:700;letter-spacing:.15em;cursor:pointer;margin:0 4px;text-transform:uppercase">⟳ RTS RESET</button>
    again, or
    <b>car battery OFF → wait 5s → ON → click RESTART</b>
  </span>
</div>

<div id="app">
  <header>
    <div class="h-brand">ROBOT_CTRL</div>
    <nav class="mode-nav">
      <button class="mode-btn active" data-mode="operate">OPERATE</button>
      <button class="mode-btn" data-mode="debug">DEBUG</button>
      <button class="mode-btn" data-mode="settings">SETTINGS</button>
    </nav>
    <div class="h-right">
      <div class="h-stat" title="WebSocket uplink"><div class="dot" id="dotLink"></div><span id="linkText">UPLINK</span></div>
      <div class="h-stat" title="ESP32 串口"><div class="dot" id="dotSerial"></div><span>SERIAL</span></div>
      <div class="h-stat" title="ESP32 心跳"><div class="dot" id="dotESP"></div><span id="espText">ESP32</span></div>
      <div class="h-stat" title="USB 音频"><div class="dot" id="dotAudio"></div><span>AUDIO</span></div>
      <div class="h-stat" title="USB 摄像头"><div class="dot" id="dotCam"></div><span>CAM</span></div>
      <div class="h-stat" title="树莓派 CPU 温度"><span style="color:var(--text-mute)">T°</span><span id="cpuTemp">--</span></div>
      <div class="h-stat" title="启动时长"><span style="color:var(--text-mute)">T+</span><span id="uptime">00:00:00</span></div>
      <button class="map-reset-btn" id="btnRestart" title="restart robot-console.service (sudo systemctl restart)"
              style="border-color:var(--red-d);color:var(--red)">⟳ RESTART</button>
    </div>
  </header>

  <section class="panel pane-cam">
    <div class="panel-head">CAM · LIVE FEED <span class="ph-r" id="camMeta">— FPS</span></div>
    <div class="panel-body">
      <div class="cam-stack layout-side" id="camStack">
        <div class="cam-frame primary" data-cam="a">
          <img class="cam-img" id="camImg" alt="" referrerpolicy="no-referrer"/>
          <svg class="cam-hud" id="camHud" viewBox="0 0 400 300" preserveAspectRatio="none">
            <g id="hudMode" class="hud-badge">
              <rect x="6" y="6" width="132" height="20" fill="rgba(0,0,0,0.65)" stroke="#6fd685" stroke-width="1"/>
              <text x="72" y="20" text-anchor="middle" font-family="JetBrains Mono, ui-monospace, Menlo, monospace" font-size="11" fill="#6fd685" letter-spacing="2" id="hudModeText">STANDBY</text>
            </g>
            <g id="hudAlert" opacity="0">
              <rect x="262" y="6" width="132" height="20" fill="rgba(255,58,46,0.88)" stroke="#ff3a2e" stroke-width="1"/>
              <text x="328" y="20" text-anchor="middle" font-family="JetBrains Mono, ui-monospace, Menlo, monospace" font-size="11" fill="#0b0907" letter-spacing="2" font-weight="700">! OBSTACLE !</text>
            </g>
            <g id="yoloBoxes"></g>
          </svg>
          <div class="cam-corner tl"></div><div class="cam-corner tr"></div>
          <div class="cam-corner bl"></div><div class="cam-corner br"></div>
          <div class="cam-id-tag" id="camTagA">CAM_A · MAIN</div>
          <button class="cam-flip-btn" data-cam-flip="a" title="正反颠倒 (180° H+V flip · 服务端 cv2.flip)·开机保留">⟲</button>
          <div class="cam-meta" id="camOSD">CAM_01 · 640x480</div>
          <div class="cam-rec"><div class="recdot"></div>LIVE</div>
          <div class="cam-overlay-err" id="camErr" style="display:none">NO_SIGNAL</div>
        </div>
        <div class="cam-frame secondary" data-cam="b" id="camFrameB" title="click to swap with main">
          <img class="cam-img" id="camImgB" alt="" referrerpolicy="no-referrer"/>
          <div class="cam-corner tl"></div><div class="cam-corner tr"></div>
          <div class="cam-corner bl"></div><div class="cam-corner br"></div>
          <div class="cam-id-tag" id="camTagB">CAM_B</div>
          <button class="cam-flip-btn" data-cam-flip="b" title="正反颠倒 (180° H+V flip · 服务端 cv2.flip)·开机保留">⟲</button>
          <div class="cam-overlay-err" id="camErrB" style="display:none">NO_SIGNAL</div>
        </div>
      </div>

      <div class="sec-label" style="margin-top:6px">MULTI-CAM · LAYOUT</div>
      <div class="cam-cfg-row">
        <span>LAYOUT</span>
        <select id="camLayoutSel">
          <option value="layout-side">SIDE×SIDE</option>
          <option value="layout-stack">STACK ↑↓</option>
          <option value="layout-pip">PIP</option>
          <option value="layout-single">SINGLE (MAIN)</option>
        </select>
        <span style="margin-left:8px">MAIN</span>
        <select id="camMainSel"></select>
      </div>

      <div class="btn-row btn-2">
        <button class="btn" id="btnMirror"><span>⟷ REAR CAM</span><span class="badge" id="mirrorBadge">OFF</span></button>
        <div class="stat-card" style="text-align:left">
          <div class="stat-label">MAIN CAM</div>
          <div class="stat-val" style="font-size:13px" id="viewLabel">CAM_A</div>
        </div>
      </div>

      <div>
        <div class="sec-label">YOLO · OBJECT DETECTION</div>
        <div class="btn-row btn-2" style="margin-bottom:6px">
          <button class="btn" id="btnYolo"><span>YOLO</span><span class="badge" id="yoloBadge">OFF</span></button>
          <div class="stat-card" style="text-align:left">
            <div class="stat-label">INFER · DETS</div>
            <div class="stat-val" style="font-size:14px" id="yoloStat">— ms · 0</div>
            <div class="mod-detail" id="yoloFilterEcho" style="font-size:9px;color:var(--text-mute);margin-top:2px;font-family:var(--font-mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">filter: all classes</div>
          </div>
        </div>
        <div class="slider-row"><span class="slider-label">CONF</span><input type="range" id="yoloConf" min="0.1" max="0.95" step="0.05" value="0.40"><span class="slider-val" id="yoloConfVal">0.40</span></div>
        <div class="slider-row"><span class="slider-label">IMGSZ</span><input type="range" id="yoloImgsz" min="192" max="640" step="32" value="416"><span class="slider-val" id="yoloImgszVal">416</span></div>
        <div class="slider-row"><span class="slider-label">RATE</span><input type="range" id="yoloRate" min="0.2" max="3.0" step="0.1" value="0.5"><span class="slider-val" id="yoloRateVal">0.5s</span></div>
        <div class="filter-row">
          <span class="slider-label">FILTER</span>
          <input type="text" id="yoloFilter" placeholder="all classes — e.g. person,car,bottle" list="yoloClassList" autocomplete="off">
        </div>
        <datalist id="yoloClassList"></datalist>
      </div>

      <div class="sys-grid">
        <div class="sys-card" id="sysCpu"><div class="sys-label">CPU °C</div><div class="sys-val" id="sysCpuVal">--</div></div>
        <div class="sys-card" id="sysLoad"><div class="sys-label">LOAD 1m</div><div class="sys-val" id="sysLoadVal">--</div></div>
        <div class="sys-card" id="sysMem"><div class="sys-label">MEM %</div><div class="sys-val" id="sysMemVal">--</div></div>
      </div>
    </div>
  </section>

  <section class="panel pane-tele">
    <div class="panel-head">TELEMETRY · MINI-MAP <span class="ph-r" id="schMode">MODE: IDLE</span>
      <button class="map-reset-btn" id="btnMapReset" title="reset map + pose">RESET</button>
    </div>
    <div class="panel-body">
      <div class="schem-wrap">
        <div class="schem-l">DEAD-RECKON · OCC-GRID</div>
        <div class="schem-r" id="schemPose">X:0 Y:0 θ:0°</div>
        <svg class="schem" id="schem" viewBox="0 0 400 320" preserveAspectRatio="xMidYMid meet">
          <defs>
            <radialGradient id="sonarG" cx="50%" cy="100%" r="100%">
              <stop offset="0%" stop-color="#6fd685" stop-opacity="0.55"/>
              <stop offset="100%" stop-color="#6fd685" stop-opacity="0"/>
            </radialGradient>
            <radialGradient id="sonarR" cx="50%" cy="100%" r="100%">
              <stop offset="0%" stop-color="#ff3a2e" stop-opacity="0.65"/>
              <stop offset="100%" stop-color="#ff3a2e" stop-opacity="0"/>
            </radialGradient>
          </defs>
          <line x1="200" y1="30" x2="200" y2="290" stroke="#5a4d2f" stroke-dasharray="2 4" opacity=".5"/>
          <line x1="60" y1="200" x2="340" y2="200" stroke="#5a4d2f" stroke-dasharray="2 4" opacity=".5"/>
          <!-- 距离环:按 MAP_SCALE=0.04 px/mm 对齐 -->
          <circle cx="200" cy="200" r="40"  fill="none" stroke="#3a2f17" stroke-dasharray="1 3"/>
          <circle cx="200" cy="200" r="80"  fill="none" stroke="#3a2f17" stroke-dasharray="1 3"/>
          <circle cx="200" cy="200" r="120" fill="none" stroke="#3a2f17" stroke-dasharray="1 3"/>
          <text x="242" y="203" fill="#5a4d2f" font-size="7" font-family="JetBrains Mono, ui-monospace, Menlo, monospace">1m</text>
          <text x="282" y="203" fill="#5a4d2f" font-size="7" font-family="JetBrains Mono, ui-monospace, Menlo, monospace">2m</text>
          <text x="322" y="203" fill="#5a4d2f" font-size="7" font-family="JetBrains Mono, ui-monospace, Menlo, monospace">3m</text>

          <!-- Mini-map world layers (动态由 JS 渲染,ego-centric: 车在 (200,200) 朝上) -->
          <polyline id="worldTrail" fill="none" stroke="#5af0ff" stroke-width="1.2" stroke-opacity="0.5" stroke-linecap="round" stroke-linejoin="round"/>
          <!-- 回放叠加层:录制时轨迹(灰虚)+ 回放时实际轨迹(亮青) -->
          <polyline id="recordedTrail" fill="none" stroke="#888" stroke-width="1.5" stroke-dasharray="3 3" stroke-opacity="0.7" stroke-linecap="round"/>
          <polyline id="replayTrail" fill="none" stroke="#6fd685" stroke-width="1.8" stroke-opacity="0.95" stroke-linecap="round"/>
          <g id="worldOcc"></g>
          <g id="worldHits"></g>
          <g id="originMarker" style="display:none">
            <circle cx="0" cy="0" r="5" fill="none" stroke="#7fff5a" stroke-width="1.2"/>
            <line x1="-7" y1="0" x2="7" y2="0" stroke="#7fff5a" stroke-width="0.8" opacity="0.8"/>
            <line x1="0" y1="-7" x2="0" y2="7" stroke="#7fff5a" stroke-width="0.8" opacity="0.8"/>
            <text x="9" y="3" font-size="7" fill="#7fff5a" font-family="JetBrains Mono, ui-monospace, Menlo, monospace" letter-spacing="0.5">ORIGIN</text>
          </g>

          <g id="vehicle" transform="translate(200 200)">
            <g id="sonar2" opacity="0" transform="rotate(-50)"></g>
            <g id="sonar3" opacity="0" transform="rotate(50)"></g>
            <g id="sonar1" opacity="0"></g>

            <rect x="-58" y="-44" width="14" height="22" fill="#3a3020" stroke="#5a4d2f"/>
            <rect x="44"  y="-44" width="14" height="22" fill="#3a3020" stroke="#5a4d2f"/>
            <rect x="-58" y="22"  width="14" height="22" fill="#3a3020" stroke="#5a4d2f"/>
            <rect x="44"  y="22"  width="14" height="22" fill="#3a3020" stroke="#5a4d2f"/>

            <rect x="-44" y="-36" width="88" height="72" fill="#1f1a10" stroke="#6fd685" stroke-width="1.5"/>
            <line x1="-44" y1="-20" x2="44" y2="-20" stroke="#5a4d2f" stroke-dasharray="2 2" opacity=".6"/>
            <line x1="-44" y1="0"   x2="44" y2="0"   stroke="#5a4d2f" stroke-dasharray="2 2" opacity=".6"/>
            <line x1="-44" y1="20"  x2="44" y2="20"  stroke="#5a4d2f" stroke-dasharray="2 2" opacity=".6"/>

            <polygon points="-10,-28 10,-28 0,-40" fill="#6fd685"/>
            <circle cx="0" cy="0" r="2.5" fill="#6fd685"/>
            <text x="0" y="12" text-anchor="middle" font-size="9" fill="#5a4d2f" font-family="JetBrains Mono, ui-monospace, Menlo, monospace">RPI-01</text>

            <g><rect x="-6" y="-40" width="12" height="5" fill="#1a1610" stroke="#6fd685"/>
               <text x="0" y="-46" text-anchor="middle" font-size="8" fill="#8a7d5e">S1</text></g>
            <g transform="rotate(-50)"><rect x="-6" y="-40" width="12" height="5" fill="#1a1610" stroke="#6fd685"/>
               <text x="0" y="-46" text-anchor="middle" font-size="8" fill="#8a7d5e">S2</text></g>
            <g transform="rotate(50)"><rect x="-6" y="-40" width="12" height="5" fill="#1a1610" stroke="#6fd685"/>
               <text x="0" y="-46" text-anchor="middle" font-size="8" fill="#8a7d5e">S3</text></g>
          </g>
          <text x="200" y="20" text-anchor="middle" font-family="JetBrains Mono, ui-monospace, Menlo, monospace" font-size="10" fill="#5a4d2f" letter-spacing="3">FRONT</text>
          <text x="200" y="312" text-anchor="middle" font-family="JetBrains Mono, ui-monospace, Menlo, monospace" font-size="10" fill="#5a4d2f" letter-spacing="3">REAR</text>

          <!-- 比例尺:40px = 1m (MAP_SCALE 0.04 px/mm) -->
          <g transform="translate(14 304)">
            <line x1="0" y1="0" x2="40" y2="0" stroke="#5a4d2f" stroke-width="1"/>
            <line x1="0" y1="-3" x2="0" y2="3" stroke="#5a4d2f" stroke-width="1"/>
            <line x1="40" y1="-3" x2="40" y2="3" stroke="#5a4d2f" stroke-width="1"/>
            <text x="20" y="-5" text-anchor="middle" font-size="7" fill="#5a4d2f" font-family="JetBrains Mono, ui-monospace, Menlo, monospace">1m</text>
          </g>
        </svg>
      </div>

      <div class="ureadings">
        <div class="ureading" data-id="1">
          <div class="ureading-label">SENSOR_01 · FRONT</div>
          <div class="ureading-row"><span class="ureading-val">----</span><span class="ureading-unit">mm</span></div>
          <div class="ureading-bar"><div style="width:0%"></div></div>
        </div>
        <div class="ureading" data-id="2">
          <div class="ureading-label">SENSOR_02 · L-FRONT</div>
          <div class="ureading-row"><span class="ureading-val">----</span><span class="ureading-unit">mm</span></div>
          <div class="ureading-bar"><div style="width:0%"></div></div>
        </div>
        <div class="ureading" data-id="3">
          <div class="ureading-label">SENSOR_03 · R-FRONT</div>
          <div class="ureading-row"><span class="ureading-val">----</span><span class="ureading-unit">mm</span></div>
          <div class="ureading-bar"><div style="width:0%"></div></div>
        </div>
      </div>
    </div>
  </section>

  <section class="panel pane-deck">
    <div class="panel-head">
      INPUT DECK
      <button class="tab-btn active" data-tab="drive">DRIVE</button>
      <button class="tab-btn" data-tab="arm">ARM</button>
      <button class="tab-btn" data-tab="rec">REC</button>
      <span class="ph-r" id="lastActChip">STOP</span>
    </div>
    <div class="panel-body">
      <div class="tab-content" id="tab-drive">
      <div class="deck-status">
        <div class="stat-card" id="cardLast">
          <div class="stat-label">LAST_ACTION</div>
          <div class="stat-val" id="lastAction">STOP</div>
        </div>
        <div class="stat-card" id="cardSpeed">
          <div class="stat-label">SPEED_PWM</div>
          <div class="stat-val" id="speedVal">----</div>
        </div>
      </div>

      <div>
        <div class="sec-label">DRIVE · WASD</div>
        <div class="key-grid">
          <div class="key empty"></div>
          <div class="key" data-press="forward" data-key="w">W<small>FWD</small></div>
          <div class="key empty"></div>
          <div class="key" data-press="left" data-key="a">A<small>LEFT</small></div>
          <div class="key" data-press="backward" data-key="s">S<small>BACK</small></div>
          <div class="key" data-press="right" data-key="d">D<small>RIGHT</small></div>
        </div>
      </div>

      <div>
        <div class="sec-label">ROTATE · Q/E</div>
        <div class="rotate-row">
          <div class="key" data-press="rotate_ccw" data-key="q">Q<small>CCW</small></div>
          <div class="key" data-press="rotate_cw" data-key="e">E<small>CW</small></div>
        </div>
      </div>

      <div>
        <div class="sec-label">MODES & SPEED</div>
        <div class="btn-row btn-2">
          <button class="btn" id="btnObs"><span>OBSTACLE</span><span class="badge" id="obsBadge">OFF</span></button>
          <button class="btn" id="btnUltra"><span>ULTRA_REPORT</span><span class="badge" id="ultraBadge">OFF</span></button>
          <button class="btn" data-action="speed_down"><span>SPEED −</span><span class="badge">[ - ]</span></button>
          <button class="btn" data-action="speed_up"><span>SPEED +</span><span class="badge">[ + ]</span></button>
          <button class="btn" data-action="speed_reset" style="grid-column:span 2;justify-content:center"><span>SPEED RESET</span><span class="badge" style="margin-left:14px">[ 0 ]</span></button>
        </div>
      </div>

      <button class="btn btn-danger" data-action="emergency">EMERGENCY STOP</button>
      </div><!-- /tab-drive -->

      <div class="tab-content" id="tab-arm" style="display:none">
        <!-- 工具栏:torque / home / save_home / readback 一行 -->
        <div class="arm-toolbar">
          <button class="btn compact" id="btnArmTorque" title="OFF=可徒手扳;ON=锁紧持位且响应 move 命令">
            ⚙ TORQUE <span class="badge" id="armTorqueBadge">OFF</span>
          </button>
          <button class="btn compact" data-arm-act="home" title="回到 arm_meta.json 里 _home_positions_deg 标定的回正姿态">⌂ HOME</button>
          <button class="btn compact" id="btnSaveHome" title="把当前 6 个 joint 位置存为新 HOME 目标(写入 arm_meta.json)">💾 SAVE HOME</button>
          <button class="btn compact" id="btnArmReadback" title="把当前实际角度填到所有 input 框">READ→INPUT</button>
        </div>
        <!-- 实测 limit 校准工具 + 救援 -->
        <div class="arm-toolbar" style="margin-top:4px">
          <button class="btn compact" id="btnArmReset" title="重置 arm controller(脱限位救援)· disconnect + 重新 from_json + connect。注意:不清 servo 内部 multi-turn 计数,要清那个需断电重启 servo 电源" style="border-color:var(--warn);color:var(--warn)">🔄 RESET ARM</button>
          <button class="btn compact" id="btnExportObs" title="导出所有 joint 浏览器记录的实测 min/max 为 JSON · 复制到剪贴板">📋 EXPORT MIN/MAX</button>
          <button class="btn compact" id="btnResetObs" title="清空浏览器记录(LocalStorage)">RESET OBS</button>
        </div>

        <!-- PRESETS · 5 slot 快速保存 / 移动 / 循环 -->
        <div>
          <div class="sec-label" style="display:flex;align-items:center;gap:6px">
            PRESETS · 5 SLOT · SPEED
            <input type="range" id="armPresetSpeed" min="20" max="360" step="10" value="180" style="flex:1">
            <span id="armPresetSpeedVal" style="font-family:var(--font-mono);font-size:10px;color:var(--amber);min-width:42px;text-align:right">180°/s</span>
          </div>
          <div class="arm-preset-row" id="armPresetRow">
            <div class="arm-preset-cell"><button class="btn preset-move" data-preset-move="0">SLOT 1</button><button class="btn compact preset-save" data-preset-save="0" title="保存当前位置">💾</button></div>
            <div class="arm-preset-cell"><button class="btn preset-move" data-preset-move="1">SLOT 2</button><button class="btn compact preset-save" data-preset-save="1" title="保存当前位置">💾</button></div>
            <div class="arm-preset-cell"><button class="btn preset-move" data-preset-move="2">SLOT 3</button><button class="btn compact preset-save" data-preset-save="2" title="保存当前位置">💾</button></div>
            <div class="arm-preset-cell"><button class="btn preset-move" data-preset-move="3">SLOT 4</button><button class="btn compact preset-save" data-preset-save="3" title="保存当前位置">💾</button></div>
            <div class="arm-preset-cell"><button class="btn preset-move" data-preset-move="4">SLOT 5</button><button class="btn compact preset-save" data-preset-save="4" title="保存当前位置">💾</button></div>
          </div>
          <div class="arm-toolbar" style="margin-top:6px">
            <button class="btn compact" id="btnArmLoopStart" title="按所有已 save 的 slot 顺序循环">▶ LOOP</button>
            <span style="font-size:9px;color:var(--text-dim)">INTERVAL</span>
            <input type="range" id="armLoopInterval" min="200" max="3000" step="100" value="800" style="flex:1">
            <span id="armLoopIntervalVal" style="font-family:var(--font-mono);font-size:10px;color:var(--amber);min-width:54px;text-align:right">800ms</span>
          </div>
        </div>

        <!-- JOINTS 主区,最常用 -->
        <div>
          <div class="sec-label">JOINTS · INPUT DEG · NUDGE ±2°</div>
          <div id="armJointGrid" style="display:grid;grid-template-columns:1fr;gap:3px;margin-top:4px"></div>
          <div style="font-size:9px;color:var(--text-mute);margin-top:4px;letter-spacing:.05em">ENTER 提交 · DBLCLICK 填当前值 · 滚轮 → 滚页面 · 需 TORQUE=ON 才动</div>
        </div>

        <!-- 笛卡尔 / GRIPPER 折叠次区 -->
        <details style="font-size:11px">
          <summary class="sec-label" style="cursor:pointer;list-style-position:inside">► CARTESIAN END EFFECTOR · ±10mm</summary>
          <div class="deck-status" style="grid-template-columns:repeat(3,1fr);margin-top:6px">
            <div class="stat-card"><div class="stat-label">X mm</div><div class="stat-val" id="armX">--</div></div>
            <div class="stat-card"><div class="stat-label">Y mm</div><div class="stat-val" id="armY">--</div></div>
            <div class="stat-card"><div class="stat-label">Z mm</div><div class="stat-val" id="armZ">--</div></div>
          </div>
          <div class="key-grid" style="grid-template-columns:repeat(3,1fr);grid-template-rows:42px 42px;margin-top:6px">
            <div class="key" data-arm-nudge="x+">+X<small>FRONT</small></div>
            <div class="key" data-arm-nudge="z+">+Z<small>UP</small></div>
            <div class="key" data-arm-nudge="y+">+Y<small>LEFT</small></div>
            <div class="key" data-arm-nudge="x-">−X<small>BACK</small></div>
            <div class="key" data-arm-nudge="z-">−Z<small>DOWN</small></div>
            <div class="key" data-arm-nudge="y-">−Y<small>RIGHT</small></div>
          </div>
        </details>

        <details style="font-size:11px">
          <summary class="sec-label" style="cursor:pointer;list-style-position:inside">► GRIPPER · ±5°</summary>
          <div class="btn-row btn-2" style="margin-top:6px">
            <button class="btn compact" data-arm-act="gripper-open">◐ OPEN +5°</button>
            <button class="btn compact" data-arm-act="gripper-close">◑ CLOSE −5°</button>
          </div>
        </details>

        <button class="btn btn-danger" data-action="emergency">EMERGENCY STOP</button>
      </div><!-- /tab-arm -->

      <div class="tab-content" id="tab-rec" style="display:none">
      <div class="deck-status">
        <div class="stat-card" id="cardRecMode">
          <div class="stat-label">REC_STATE</div>
          <div class="stat-val" id="recPanelState" style="font-size:13px">IDLE</div>
        </div>
        <div class="stat-card" id="cardRecCnt">
          <div class="stat-label">EVENTS · DUR</div>
          <div class="stat-val" id="recPanelMeta" style="font-size:13px">0 · 0.0s</div>
        </div>
      </div>

      <div class="rec-progress-row" id="recProgRow" style="display:none">
        <div class="rec-progress-track"><div class="rec-progress-fill" id="recProgFill"></div></div>
        <div class="rec-progress-meta" id="recProgMeta">— / —</div>
      </div>
      <div class="rec-current" id="recCurrent" style="display:none">▶ <span id="recCurrentText">—</span></div>

      <div>
        <div class="sec-label">RECORD · MEMORY · MAX 10MIN</div>
        <div class="btn-row btn-2" style="margin-top:6px">
          <button class="btn" id="btnRecToggle">● REC</button>
          <button class="btn compact" id="btnRecClear" disabled>CLEAR</button>
        </div>
      </div>

      <div>
        <div class="sec-label">PLAYBACK</div>
        <div class="btn-row btn-3" style="margin-top:6px">
          <button class="btn" id="btnRecPlayFwd" disabled>▶ PLAY</button>
          <button class="btn" id="btnRecPlayRev" disabled>◀ REVERSE</button>
          <button class="btn compact" id="btnRecStopPlay" disabled>■ STOP</button>
        </div>
        <div class="slider-row" style="margin-top:8px">
          <span class="slider-label">SPD</span>
          <input type="range" id="recSpeed" min="0.25" max="4" step="0.25" value="1">
          <span class="slider-val" id="recSpeedVal">1.00×</span>
        </div>
        <label class="cal-toggle">
          <input type="checkbox" id="recCalibrate">
          <span>CALIBRATE · ULTRA Δ VISUALIZATION</span>
        </label>
        <div class="slider-row" style="margin-top:6px" title="重心前置 → 后驱效率较低。降低此值倒车段拉长、distance API 自动补偿、minimap dead-reckoning 也用它">
          <span class="slider-label">BACK_R</span>
          <input type="range" id="backRatio" min="0.3" max="1.2" step="0.05" value="0.7">
          <span class="slider-val" id="backRatioVal">0.70</span>
        </div>
      </div>

      <div id="recCalDiff" class="rec-cal-diff" style="display:none">
        <div class="sec-label">ULTRA · REC vs LIVE (mm)</div>
        <div class="rec-cal-row"><span>S1·F</span><span id="recCalRec1">—</span><span id="recCalLive1">—</span><span id="recCalDiff1">—</span></div>
        <div class="rec-cal-row"><span>S2·L</span><span id="recCalRec2">—</span><span id="recCalLive2">—</span><span id="recCalDiff2">—</span></div>
        <div class="rec-cal-row"><span>S3·R</span><span id="recCalRec3">—</span><span id="recCalLive3">—</span><span id="recCalDiff3">—</span></div>
      </div>
      </div><!-- /tab-rec -->
    </div>
  </section>

  <section class="panel pane-log">
    <div class="panel-head">EVENT_STREAM · UPLINK BUFFER <span class="ph-r" id="logCount">0 lines</span></div>
    <div class="panel-body">
      <div class="log-body" id="logBody"></div>
    </div>
  </section>

  <section class="panel pane-side">
    <div class="panel-head">AUDIO · VOICE I/O</div>
    <div class="panel-body">
      <div class="deck-status">
        <div class="stat-card" id="cardRec">
          <div class="stat-label">REC_STATE</div>
          <div class="stat-val" id="recState">IDLE</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">DEVICE</div>
          <div class="stat-val" style="font-size:13px">USB_AUDIO</div>
        </div>
      </div>

      <div>
        <div class="sec-label">VOLUME · ALSA MIXER</div>
        <div class="slider-row"><span class="slider-label">SPK</span><input type="range" id="volSpk" min="0" max="100" step="2" value="50"><span class="slider-val" id="volSpkVal">--%</span></div>
        <div class="slider-row"><span class="slider-label">MIC</span><input type="range" id="volMic" min="0" max="100" step="2" value="50"><span class="slider-val" id="volMicVal">--%</span></div>
      </div>

      <div>
        <div class="sec-label">CAT_VOICE · [ 1 / 2 / 3 / 4 ]</div>
        <div class="btn-row btn-4">
          <button class="btn compact" data-action="cat1">CAT_1</button>
          <button class="btn compact" data-action="cat2">CAT_2</button>
          <button class="btn compact" data-action="cat3">CAT_3</button>
          <button class="btn compact" data-action="cat4">CAT_4</button>
        </div>
      </div>

      <div>
        <div class="sec-label">MICROPHONE</div>
        <div class="btn-row btn-2">
          <button class="btn" id="btnRec"><span>● REC</span><span class="badge">HOLD R</span></button>
          <button class="btn" id="btnStopAudio"><span>■ STOP</span><span class="badge">[ ESC ]</span></button>
        </div>
      </div>

      <div>
        <div class="sec-label" style="display:flex;justify-content:space-between;align-items:center;gap:6px">
          <span>UPLOADS · MP3 / WAV / FLAC</span>
          <label class="map-reset-btn" style="cursor:pointer;border-color:var(--amber-d);color:var(--amber);margin-left:0">
            + UPLOAD<input type="file" id="uploadFile" accept=".mp3,.wav,.ogg,.flac,.m4a,.aac" style="display:none">
          </label>
        </div>
        <div class="audio-list" id="uploadsList" style="max-height:130px"><div class="audio-empty">— EMPTY —</div></div>
      </div>

      <div style="flex:1;display:flex;flex-direction:column;min-height:0">
        <div class="sec-label" style="display:flex;justify-content:space-between;align-items:center;gap:6px">
          <span>RECORDINGS · CLICK TO PLAY</span>
          <button class="map-reset-btn" id="btnClearRecs" style="border-color:var(--red-d);color:var(--red);margin-left:0" title="delete all recordings">⌫ CLEAR</button>
        </div>
        <div class="audio-list" id="recList"><div class="audio-empty">— EMPTY —</div></div>
      </div>
    </div>
  </section>
</div>

<script>
(() => {
  const $ = q => document.querySelector(q);
  const $$ = q => document.querySelectorAll(q);
  const startTime = Date.now();

  // ============ Camera image (multi-cam) ============
  // 每个 <img> 走自己的 /api/camera/{id}/stream.mjpg。错时 1.5s 重连(cache-buster)。
  function startCamImg(imgId, errId, camId){
    const img = $('#' + imgId);
    const errEl = $('#' + errId);
    const url = () => `/api/camera/${camId}/stream.mjpg?t=${Date.now()}`;
    img.onerror = () => { errEl.style.display = 'flex'; setTimeout(() => { img.src = url(); }, 1500); };
    img.onload = () => { errEl.style.display = 'none'; };
    img.src = url();
  }
  // 跟主/副 frame 的 data-cam 联动 — 启动时按 frame 上的 data-cam 拉对应流
  function rebindCamImgs(){
    document.querySelectorAll('.cam-frame').forEach(fr => {
      const cid = fr.dataset.cam;
      const img = fr.querySelector('.cam-img');
      const err = fr.querySelector('.cam-overlay-err');
      if (!cid || !img) return;
      const newSrc = `/api/camera/${cid}/stream.mjpg?t=${Date.now()}`;
      if (img.src && img.src.includes(`/api/camera/${cid}/`)) return;  // 已经在播该 cam
      img.onerror = () => { if (err) err.style.display = 'flex'; setTimeout(() => { img.src = `/api/camera/${cid}/stream.mjpg?t=${Date.now()}`; }, 1500); };
      img.onload = () => { if (err) err.style.display = 'none'; };
      img.src = newSrc;
    });
  }
  rebindCamImgs();
  // Frontend watchdog:每 5 秒检查每个 cam img,naturalWidth==0 或 complete=false 时强制
  // reset src(MJPEG 长连接在浏览器端有时静默卡死,onerror 不触发 → img 一直黑)
  setInterval(() => {
    document.querySelectorAll('.cam-frame').forEach(fr => {
      const cid = fr.dataset.cam;
      const img = fr.querySelector('.cam-img');
      const err = fr.querySelector('.cam-overlay-err');
      if (!cid || !img) return;
      // 隐藏的 frame (single layout 副) 跳过
      if (fr.offsetParent === null) return;
      const broken = (!img.complete) || img.naturalWidth === 0;
      if (broken){
        if (err) err.style.display = 'flex';
        img.src = `/api/camera/${cid}/stream.mjpg?t=${Date.now()}`;
      }
    });
  }, 5000);

  // ============ Mode nav (OPERATE / DEBUG / SETTINGS) ============
  const MODE_KEY = 'consoleMode';
  function applyMode(mode){
    if (!['operate','debug','settings'].includes(mode)) mode = 'operate';
    document.body.classList.remove('mode-operate','mode-debug','mode-settings');
    document.body.classList.add('mode-' + mode);
    document.querySelectorAll('.mode-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.mode === mode);
    });
    localStorage.setItem(MODE_KEY, mode);
    // 切到 OPERATE 时 deck 自动跳 DRIVE 子 tab
    if (mode === 'operate'){
      const driveBtn = document.querySelector('.tab-btn[data-tab="drive"]');
      if (driveBtn && !driveBtn.classList.contains('active')) driveBtn.click();
    }
  }
  document.querySelectorAll('.mode-btn').forEach(b => {
    b.addEventListener('click', () => applyMode(b.dataset.mode));
  });
  applyMode(localStorage.getItem(MODE_KEY) || 'operate');

  // log panel head 点击 toggle(operate/settings mode 下默认折叠)
  const logPanelHead = document.querySelector('.pane-log .panel-head');
  if (logPanelHead){
    logPanelHead.addEventListener('click', ev => {
      const isCollapsed = document.body.classList.contains('mode-operate') || document.body.classList.contains('mode-settings');
      if (!isCollapsed) return;  // debug mode 不需要 toggle
      const body = document.querySelector('.pane-log .panel-body');
      if (body) body.style.display = body.style.display === 'none' || !body.style.display ? 'flex' : 'none';
    });
  }

  // ============ Multi-cam config (layout + main) ============
  const CAM_LAYOUT_KEY = 'camLayout';
  const CAM_FRAME_ORDER_KEY = 'camFrameOrder';  // 记 [primaryId, secondaryId]
  function applyCamLayout(layoutCls){
    const stack = $('#camStack');
    if (!stack) return;
    ['layout-side','layout-stack','layout-pip','layout-single'].forEach(c => stack.classList.remove(c));
    stack.classList.add(layoutCls);
    localStorage.setItem(CAM_LAYOUT_KEY, layoutCls);
    $('#camLayoutSel').value = layoutCls;
  }
  function setPrimaryCam(camId){
    const stack = $('#camStack');
    if (!stack) return;
    const frames = stack.querySelectorAll('.cam-frame');
    let primaryEl = null, secondaryEl = null;
    frames.forEach(fr => {
      if (fr.dataset.cam === camId){ fr.classList.add('primary'); fr.classList.remove('secondary'); primaryEl = fr; }
      else { fr.classList.remove('primary'); fr.classList.add('secondary'); secondaryEl = fr; }
    });
    // DOM 顺序:primary 在前(side/stack/pip 都需要)
    if (primaryEl && secondaryEl && stack.children[0] !== primaryEl){
      stack.insertBefore(primaryEl, secondaryEl);
    }
    // 标签更新
    document.querySelectorAll('.cam-id-tag').forEach(t => {
      const fr = t.closest('.cam-frame');
      if (!fr) return;
      const cid = fr.dataset.cam.toUpperCase();
      t.textContent = fr.classList.contains('primary') ? `CAM_${cid} · MAIN` : `CAM_${cid}`;
    });
    // mirror 跟随主 cam
    applyRearMode();
    localStorage.setItem(CAM_FRAME_ORDER_KEY, camId);
  }
  async function camListInit(){
    try {
      const r = await fetch('/api/camera/list'); const d = await r.json();
      const sel = $('#camMainSel');
      sel.innerHTML = '';
      (d.cameras || []).forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.id;
        opt.textContent = `CAM_${c.id.toUpperCase()}${c.ok ? '' : ' · DOWN'}`;
        if (c.is_main) opt.selected = true;
        sel.appendChild(opt);
        // sync flip 按钮状态
        const flipBtn = document.querySelector(`.cam-flip-btn[data-cam-flip="${c.id}"]`);
        if (flipBtn) flipBtn.classList.toggle('on', c.flipped);
      });
      // 只有 1 个 cam 时隐藏副 frame + main select
      const camCount = (d.cameras || []).filter(c => c.ok).length;
      if (camCount < 2){
        const sec = document.querySelector('.cam-frame.secondary');
        if (sec) sec.style.display = 'none';
        sel.disabled = true;
      }
      // 应用持久化偏好
      const savedMain = localStorage.getItem(CAM_FRAME_ORDER_KEY) || d.main_id;
      if (savedMain && (d.cameras || []).find(c => c.id === savedMain && c.ok)){
        setPrimaryCam(savedMain);
        if (savedMain !== d.main_id){
          try { await fetch('/api/camera/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({main_id: savedMain})}); } catch(e){}
          sel.value = savedMain;
        }
      }
      const savedLayout = localStorage.getItem(CAM_LAYOUT_KEY) || 'layout-side';
      applyCamLayout(savedLayout);
    } catch(e){ console.error('camList init', e); }
  }
  camListInit();
  $('#camLayoutSel').addEventListener('change', ev => applyCamLayout(ev.target.value));
  $('#camMainSel').addEventListener('change', async ev => {
    const newMain = ev.target.value;
    try { await fetch('/api/camera/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({main_id: newMain})}); } catch(e){}
    setPrimaryCam(newMain);
  });
  // 翻转按钮 click → POST flipped toggle + 状态切回(server-side cv2.flip 真翻 frame)
  document.querySelectorAll('.cam-flip-btn').forEach(b => {
    b.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      const cid = b.dataset.camFlip;
      const next = !b.classList.contains('on');
      try {
        const r = await fetch('/api/camera/config', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({cam_id: cid, flipped: next})});
        if (r.ok) b.classList.toggle('on', next);
      } catch(e){}
    });
  });
  // PIP 模式下 click 副 frame = swap main
  document.addEventListener('click', ev => {
    const stack = $('#camStack');
    if (!stack || !stack.classList.contains('layout-pip')) return;
    const sec = ev.target.closest('.cam-frame.secondary');
    if (!sec) return;
    const newMain = sec.dataset.cam;
    const sel = $('#camMainSel');
    if (sel) { sel.value = newMain; sel.dispatchEvent(new Event('change')); }
  });

  // ============ WebSocket ============
  let ws = null;
  function connect(){
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen  = () => { $('#dotLink').className = 'dot ok';    $('#linkText').textContent = 'ONLINE'; };
    ws.onclose = () => { $('#dotLink').className = 'dot error'; $('#linkText').textContent = 'OFFLINE'; setTimeout(connect, 1200); };
    ws.onerror = () => {};
    ws.onmessage = ev => {
      let m; try{ m = JSON.parse(ev.data); }catch(e){ return; }
      if (m.type === 'snapshot'){ applyState(m.data.state); ($('#logBody')).innerHTML=''; (m.data.logs||[]).forEach(addLog); }
      else if (m.type === 'state'){ applyState(m.data); }
      else if (m.type === 'ultra'){ applyUltra(m.data); }
      else if (m.type === 'log'){ addLog(m.data); }
      else if (m.type === 'yolo'){
        if (!yoloEnabled){
          // YOLO 已关闭,任何残留 WS yolo event 都 ignore + 强制清空 SVG
          const layer = document.getElementById('yoloBoxes');
          if (layer && layer.innerHTML) layer.innerHTML = '';
          return;
        }
        renderYoloBoxes(m.data.detections);
        $('#yoloStat').textContent = `${m.data.inference_ms}ms · ${m.data.detections.length}`;
      }
      else if (m.type === 'map'){ applyMap(m.data); }
      else if (m.type === 'rec'){ applyRec(m.data); }
      else if (m.type === 'volume'){
        const v = m.data || {};
        if (v.speaker != null){ volSpk.value = v.speaker; volSpkVal.textContent = v.speaker + '%'; }
        if (v.mic != null){ volMic.value = v.mic; volMicVal.textContent = v.mic + '%'; }
      }
    };
  }
  connect();

  // ============ API ============
  async function cmd(action){
    try{ await fetch(`/api/cmd/${action}`, {method:'POST'}); } catch(e){ console.error(e); }
  }
  async function audioApi(path, body){
    try{
      const opts={method:'POST'};
      if(body){ opts.headers={'Content-Type':'application/json'}; opts.body=JSON.stringify(body); }
      const r=await fetch(path,opts);
      const data = r.ok ? await r.json().catch(()=>null) : null;
      return {ok: r.ok, status: r.status, body: data};
    }catch(e){return {ok: false, status: 0, body: null};}
  }

  // ============ Recording state machine (optimistic + revert) ============
  async function startRec(){
    if (recording) return false;
    recording = true;
    $('#btnRec').classList.add('on');
    const r = await audioApi('/api/audio/rec/start');
    // server says we were already recording -> trust server (still on)
    if (!r.ok){
      recording = false;
      $('#btnRec').classList.remove('on');
      return false;
    }
    // server may have refused (already recording / hardware busy) — body.ok=false
    if (r.body && r.body.ok === false){
      // keep flag in sync with whatever server thinks (state push will follow)
      // but our optimistic flip was wrong; let WS state correct us
    }
    return true;
  }
  async function stopRec(){
    if (!recording) return;
    recording = false;
    $('#btnRec').classList.remove('on');
    await audioApi('/api/audio/rec/stop');
    loadRecs();
  }

  // ============ Emergency (immediate UI sync) ============
  function doEmergency(){
    cmd('emergency');
    // 别等服务器推送,立即清前端互斥状态
    modeObs = false; modeUltra = false;
    $('#btnObs').classList.remove('on'); $('#obsBadge').textContent = 'OFF';
    $('#btnUltra').classList.remove('on'); $('#ultraBadge').textContent = 'OFF';
  }

  // ============ Health poll ============
  async function pollHealth(){
    try{
      const r = await fetch('/api/health');
      const h = await r.json();
      applyHealth(h);
    }catch(e){
      ['serial','esp32','audio','camera'].forEach(k => setDot(k, 'error'));
    }
  }
  setInterval(pollHealth, 2000);
  pollHealth();

  function setDot(key, level){
    const dotMap = {serial:'dotSerial', esp32:'dotESP', audio:'dotAudio', camera:'dotCam'};
    const dot = $('#' + dotMap[key]);
    if (dot) dot.className = 'dot ' + (level === 'ok' ? 'ok' : level === 'warn' ? 'warn' : 'error');
  }

  function applyHealth(h){
    const m = h.modules || {};
    setDot('serial', m.serial?.ok ? 'ok' : 'error');
    setDot('esp32',  m.esp32?.ok ? 'ok' : 'warn');
    setDot('audio',  m.audio?.ok ? 'ok' : 'error');
    const camOk = m.camera?.ok;
    setDot('camera', camOk ? 'ok' : 'error');
    $('#camMeta').textContent = camOk ? `${(m.camera?.fps||0).toFixed(1)} FPS · ${(m.camera?.frame_age_ms||0)}ms ago` : 'NO SIGNAL';
    $('#camOSD').textContent = m.camera?.detail || 'CAM_OFFLINE';
    if (!camOk) $('#camErr').style.display = 'flex';

    const s = h.system || {};
    $('#sysCpuVal').textContent = s.cpu_temp_c != null ? s.cpu_temp_c.toFixed(1) : '--';
    $('#sysCpu').classList.toggle('warn', s.cpu_temp_c != null && s.cpu_temp_c > 70);
    $('#sysLoadVal').textContent = s.load_1m != null ? s.load_1m.toFixed(2) : '--';
    $('#sysLoad').classList.toggle('warn', s.load_1m != null && s.load_1m > 3);
    $('#sysMemVal').textContent = s.mem_pct != null ? s.mem_pct.toFixed(0) + '%' : '--';
    $('#sysMem').classList.toggle('warn', s.mem_pct != null && s.mem_pct > 85);
    $('#cpuTemp').textContent = s.cpu_temp_c != null ? s.cpu_temp_c.toFixed(1) + '°C' : '--';
  }

  // ============ Uptime ============
  setInterval(() => {
    const s = Math.floor((Date.now() - startTime) / 1000);
    const hh=String(Math.floor(s/3600)).padStart(2,'0');
    const mm=String(Math.floor(s/60)%60).padStart(2,'0');
    const ss=String(s%60).padStart(2,'0');
    $('#uptime').textContent = `${hh}:${mm}:${ss}`;
  }, 500);

  // ============ REAR CAM mode (摄像头朝车后方装的视角校正) ============
  // 开启时:视频水平镜像;WASD W↔S 反转、QE 反转、AD 保持;YOLO bbox 也镜像 x
  // 让用户的 FPS 直觉(按 W 画面里东西靠近)跟实际车控制对应起来
  let rearMode = localStorage.getItem('rearMode') === 'true';
  function applyRearMode(){
    // mirror 始终作用主 cam frame(.primary),副 cam 不动
    document.querySelectorAll('.cam-frame').forEach(fr => fr.classList.remove('mirror'));
    const primary = document.querySelector('.cam-frame.primary');
    if (primary && rearMode) primary.classList.add('mirror');
    // viewLabel 跟主 cam id 联动:CAM_A · REAR / CAM_B 等
    const lbl = $('#viewLabel');
    if (lbl){
      const mainId = (primary?.dataset.cam || 'a').toUpperCase();
      lbl.textContent = `CAM_${mainId}${rearMode ? ' · REAR' : ''}`;
    }
    const btn = $('#btnMirror');
    if (btn) btn.classList.toggle('on', rearMode);
    const badge = $('#mirrorBadge');
    if (badge) badge.textContent = rearMode ? 'ON' : 'OFF';
  }
  function toggleRearMode(){
    // 切换前先停止所有 hold,避免 stopHold(反转后) 找不到 timer 残留
    pressed.forEach((action, k) => { stopHold(action); highlightKey(k, false); });
    pressed.clear();
    rearMode = !rearMode;
    localStorage.setItem('rearMode', String(rearMode));
    applyRearMode();
  }

  // ============ Hold-to-move ============
  const moveMapNormal = {
    'w':'forward','arrowup':'forward',
    's':'backward','arrowdown':'backward',
    'a':'left','arrowleft':'left',
    'd':'right','arrowright':'right',
    'q':'rotate_ccw','e':'rotate_cw',
  };
  const moveMapRear = {
    // 反转 W↔S 让"按 W 画面里东西靠近"
    'w':'backward','arrowup':'backward',
    's':'forward','arrowdown':'forward',
    // 反转 A↔D:cam 朝后 + 画面 scaleX(-1) 镜像后,用户感知的"画面左滑"= 车体物理右移
    'a':'right','arrowleft':'right',
    'd':'left','arrowright':'left',
    // 反转 Q↔E 让旋转方向跟画面世界滚动方向一致
    'q':'rotate_cw','e':'rotate_ccw',
  };
  function moveMap(){ return rearMode ? moveMapRear : moveMapNormal; }
  // Hold 由后端 thread 控制,前端只发 down/renew/up 边沿(WS),避免网络抖动让 cmd 间隔不稳。
  // 网络延迟只影响 down/up 边沿(瞬时),中间走的距离由后端本地稳定 100ms 计时决定。
  const holdRenewers = {};
  const HOLD_RENEW_MS = 600;  // < 后端 1.5s timeout,有冗余
  function wsSend(obj){
    if (ws && ws.readyState === 1){
      try { ws.send(JSON.stringify(obj)); return true; } catch(e){}
    }
    return false;
  }
  function startHold(action){
    if (holdRenewers[action]) return;
    if (!wsSend({type:'hold', state:'down', action})){
      // WS 没连上时降级到 HTTP one-shot
      cmd(action);
      return;
    }
    holdRenewers[action] = setInterval(() => {
      wsSend({type:'hold', state:'renew', action});
    }, HOLD_RENEW_MS);
  }
  function stopHold(action){
    if (!holdRenewers[action]) return;
    clearInterval(holdRenewers[action]);
    delete holdRenewers[action];
    // 单键释放:剩余 hold action 仍由后端 round-robin 继续
    wsSend({type:'hold', state:'up', action});
    if (Object.keys(holdRenewers).length === 0){
      // 全松开时兜底:HTTP stop 防 WS 抖动
      try{ navigator.sendBeacon('/api/cmd/stop'); }catch(e){}
    }
  }

  // ============ Keyboard ============
  const pressed = new Map();  // key -> action(根据 rearMode 决定的实际 cmd)
  let recording = false;
  document.addEventListener('keydown', ev => {
    if (ev.repeat) return;
    const k = ev.key.toLowerCase();
    // ESC 是全局停止键,永远生效(即使焦点在 YOLO filter input 框)
    if (k === 'escape'){
      cmd('stop');
      audioApi('/api/audio/stop');
      if (ev.target && ev.target.blur) ev.target.blur();
      ev.preventDefault();
      return;
    }
    if (ev.target && (ev.target.tagName === 'INPUT' || ev.target.tagName === 'TEXTAREA')) return;
    const action = moveMap()[k];
    if (action){
      if (pressed.has(k)) return;
      pressed.set(k, action);
      startHold(action); highlightKey(k, true);
      ev.preventDefault(); return;
    }
    switch(k){
      case ' ': doEmergency(); ev.preventDefault(); break;
      case 'o': setObs(true); break;
      case 'p': setObs(false); break;
      case 'u': setUltra(true); break;
      case 'v': setUltra(false); break;
      case '+': case '=': cmd('speed_up'); break;
      case '-': case '_': cmd('speed_down'); break;
      case '0': case 'm': cmd('speed_reset'); break;
      case '1': cmd('cat1'); break;
      case '2': cmd('cat2'); break;
      case '3': cmd('cat3'); break;
      case '4': cmd('cat4'); break;
      case 'r': startRec(); break;
    }
  });
  document.addEventListener('keyup', ev => {
    const k = ev.key.toLowerCase();
    if (pressed.has(k)){
      const action = pressed.get(k);
      pressed.delete(k);
      stopHold(action);
      highlightKey(k, false);
    }
    if (k === 'r') stopRec();
  });
  window.addEventListener('blur', () => {
    pressed.forEach((action, k) => { stopHold(action); highlightKey(k, false); });
    pressed.clear();
    stopRec();
  });

  // 关 tab / 刷新 / 关浏览器时,保证 stop recording 一定发出 (sendBeacon 比 fetch 可靠)
  window.addEventListener('beforeunload', () => {
    if (recording){
      try{ navigator.sendBeacon('/api/audio/rec/stop'); }catch(e){}
    }
    // 同时停止任何 hold 中的方向 cmd
    if (Object.keys(holdTimers).length > 0){
      try{ navigator.sendBeacon('/api/cmd/stop'); }catch(e){}
    }
  });
  function highlightKey(k, on){
    const el = document.querySelector(`.key[data-key="${k}"]`);
    if (el) el.classList.toggle('active', on);
  }

  // ============ Mouse/touch keys (rearMode 时反转 W/S 和 Q/E) ============
  const rearActionMap = {
    forward: 'backward', backward: 'forward',
    rotate_ccw: 'rotate_cw', rotate_cw: 'rotate_ccw',
    left: 'right', right: 'left',
  };
  $$('.key[data-press]').forEach(el => {
    let currentHoldAction = null;
    const resolve = () => rearMode ? (rearActionMap[el.dataset.press] || el.dataset.press) : el.dataset.press;
    const down = ev => {
      ev.preventDefault();
      currentHoldAction = resolve();
      startHold(currentHoldAction);
      el.classList.add('active');
    };
    const up = () => {
      if (currentHoldAction){ stopHold(currentHoldAction); currentHoldAction = null; }
      el.classList.remove('active');
    };
    el.addEventListener('mousedown', down);
    el.addEventListener('mouseup', up);
    el.addEventListener('mouseleave', up);
    el.addEventListener('touchstart', down, {passive:false});
    el.addEventListener('touchend', up);
    el.addEventListener('touchcancel', up);
  });

  // REAR CAM toggle 按钮(无键盘快捷键,避免跟 m=speed_reset / 字母键冲突)
  $('#btnMirror').addEventListener('click', () => toggleRearMode());
  applyRearMode();  // 初始化 UI 反映 localStorage 状态

  // ============ Action buttons ============
  $$('button.btn[data-action]').forEach(el => {
    el.addEventListener('click', ev => {
      ev.preventDefault();
      const a = el.dataset.action;
      if (a === 'emergency') doEmergency();
      else cmd(a);
    });
  });

  // ============ Toggles ============
  // 后端 OBSTACLE 已改为"软件避障"(内部启 ULTRA_REPORT + 服务端检距停车),
  // 跟 ULTRA_REPORT 不互斥了 — 前端不再需要先 off 再 on 的复合操作。
  let modeObs = false, modeUltra = false;
  function setObs(on){ cmd(on ? 'obstacle_on' : 'obstacle_off'); }
  function setUltra(on){ cmd(on ? 'ultra_on' : 'ultra_off'); }
  $('#btnObs').addEventListener('click', () => setObs(!modeObs));
  $('#btnUltra').addEventListener('click', () => setUltra(!modeUltra));

  // ============ Recording button (hold) ============
  const btnRec = $('#btnRec');
  const recDown = ev => { ev.preventDefault(); startRec(); };
  const recUp   = () => { stopRec(); };
  btnRec.addEventListener('mousedown', recDown);
  btnRec.addEventListener('mouseup', recUp);
  btnRec.addEventListener('mouseleave', recUp);
  btnRec.addEventListener('touchstart', recDown, {passive:false});
  btnRec.addEventListener('touchend', recUp);
  btnRec.addEventListener('touchcancel', recUp);
  $('#btnStopAudio').addEventListener('click', () => audioApi('/api/audio/stop'));

  // ============ Volume sliders ============
  const volSpk = $('#volSpk'), volSpkVal = $('#volSpkVal');
  const volMic = $('#volMic'), volMicVal = $('#volMicVal');
  let volDebounce = null;
  function volPost(body){
    clearTimeout(volDebounce);
    volDebounce = setTimeout(() => {
      fetch('/api/audio/volume', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(body),
      }).catch(() => {});
    }, 150);
  }
  volSpk.addEventListener('input', e => volSpkVal.textContent = e.target.value + '%');
  volSpk.addEventListener('change', e => volPost({speaker: parseInt(e.target.value)}));
  volMic.addEventListener('input', e => volMicVal.textContent = e.target.value + '%');
  volMic.addEventListener('change', e => volPost({mic: parseInt(e.target.value)}));
  // 初始拉
  fetch('/api/audio/volume').then(r => r.ok ? r.json() : null).then(v => {
    if (!v) return;
    if (v.speaker != null){ volSpk.value = v.speaker; volSpkVal.textContent = v.speaker + '%'; }
    if (v.mic != null){ volMic.value = v.mic; volMicVal.textContent = v.mic + '%'; }
  }).catch(() => {});

  // ============ Recordings list ============
  async function loadRecs(){
    try{
      const r = await fetch('/api/recordings'); const j = await r.json();
      const box = $('#recList');
      if (!j.items || j.items.length===0){ box.innerHTML='<div class="audio-empty">— EMPTY —</div>'; return; }
      box.innerHTML = '';
      j.items.forEach(it => {
        const row = document.createElement('div');
        row.className = 'audio-item';
        const kb = (it.size/1024).toFixed(0);
        const ts = new Date(it.mtime*1000);
        const ago = formatAgo(ts);
        row.innerHTML = `<span>▸ ${escapeHtml(it.name)}</span><span class="size">${kb}K · ${ago}</span>`;
        row.onclick = () => audioApi('/api/audio/play', {name: it.name, src: 'recordings'});
        box.appendChild(row);
      });
    } catch(e){}
  }
  loadRecs(); setInterval(loadRecs, 6000);

  // ============ Uploaded music (mp3/wav/...) ============
  async function loadUploads(){
    try {
      const r = await fetch('/api/audio/uploads'); const j = await r.json();
      const box = $('#uploadsList');
      if (!j.items || j.items.length===0){ box.innerHTML='<div class="audio-empty">— EMPTY —</div>'; return; }
      box.innerHTML = '';
      j.items.forEach(it => {
        const row = document.createElement('div');
        row.className = 'audio-item';
        const kb = (it.size/1024).toFixed(0);
        row.innerHTML = `<span>▸ ${escapeHtml(it.name)}</span><span class="size">${kb}K  <span style="color:var(--red);margin-left:4px;cursor:pointer" data-del="${escapeHtml(it.name)}">✕</span></span>`;
        row.querySelector('[data-del]').addEventListener('click', async (ev) => {
          ev.stopPropagation();
          if (!confirm(`Delete "${it.name}"?`)) return;
          await fetch('/api/audio/upload/' + encodeURIComponent(it.name), {method:'DELETE'});
          loadUploads();
        });
        row.addEventListener('click', () => audioApi('/api/audio/play', {name: it.name, src: 'uploads'}));
        box.appendChild(row);
      });
    } catch(e){}
  }
  loadUploads(); setInterval(loadUploads, 8000);

  $('#uploadFile').addEventListener('change', async (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f);
    try {
      const r = await fetch('/api/audio/upload', {method:'POST', body: fd});
      if (r.ok) {
        loadUploads();
      } else {
        const txt = await r.text();
        alert('upload failed: ' + r.status + ' ' + txt);
      }
    } catch(err){ alert('upload error: ' + err.message); }
  });

  $('#btnClearRecs').addEventListener('click', async () => {
    if (!confirm('Delete ALL recordings? This cannot be undone.')) return;
    try {
      const r = await fetch('/api/recordings', {method:'DELETE'});
      const j = await r.json();
      console.log('cleared', j.removed, 'recordings');
      loadRecs();
    } catch(e){}
  });

  function formatAgo(d){
    const dt = (Date.now()-d.getTime())/1000;
    if (dt<60) return `${Math.floor(dt)}s`;
    if (dt<3600) return `${Math.floor(dt/60)}m`;
    if (dt<86400) return `${Math.floor(dt/3600)}h`;
    return `${Math.floor(dt/86400)}d`;
  }
  function escapeHtml(s){ return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]); }

  // ============ State / telemetry ============
  function applyState(s){
    if (!s) return;
    if (s.modes){
      modeObs = !!s.modes.obstacle; modeUltra = !!s.modes.ultra_report;
      $('#btnObs').classList.toggle('on', modeObs);
      $('#obsBadge').textContent = modeObs ? 'ON' : 'OFF';
      $('#btnUltra').classList.toggle('on', modeUltra);
      $('#ultraBadge').textContent = modeUltra ? 'ON' : 'OFF';
    }
    if (s.last_action){
      $('#lastAction').textContent = s.last_action;
      $('#lastActChip').textContent = s.last_action;
    }
    if (s.speed != null) $('#speedVal').textContent = String(s.speed);
    const rec = !!s.recording;
    $('#recState').textContent = rec ? 'RECORDING' : 'IDLE';
    $('#cardRec').classList.toggle('on', rec);
    const em = s.modes && s.modes.emergency;
    $('#espText').textContent = em ? 'E-STOP' : 'ESP32';
    $('#cardLast').classList.toggle('danger', em);
    let mode = 'IDLE';
    if (em) mode = 'E-STOP';
    else if (modeObs) mode = 'AUTO-AVOID';
    else if (modeUltra) mode = 'TELEMETRY';
    else if (s.last_action && s.last_action !== 'STOP') mode = s.last_action;
    $('#schMode').textContent = `MODE: ${mode}`;
    updateHudMode(em);
    // ESP32 firmware deadlock banner
    const banner = document.getElementById('deadlockBanner');
    if (banner){
      banner.style.display = s.esp32_deadlock ? 'block' : 'none';
    }
    if (s.ultra) applyUltra(s.ultra);
  }

  // ============ MiniMap (dead-reckoning + occupancy grid) ============
  // ego-centric:车始终在 SVG 中心 (200, 200) 朝上,世界相对车反向滚动
  const MAP_SCALE = 0.04;   // px / mm → 400px viewBox 覆盖 10m
  const CAR_CX = 200, CAR_CY = 200;

  function worldToSvg(wx, wy, pose){
    // 数学坐标 (+x 前, +y 左, θ 逆时针) → ego-centric SVG (车在中心朝上)
    // SVG +x 右、+y 下,所以:车前 → SVG -y, 车左 → SVG -x
    const dx = wx - pose.x;
    const dy = wy - pose.y;
    const c = Math.cos(pose.theta);
    const s = Math.sin(pose.theta);
    const local_x = dx * c + dy * s;   // 前距
    const local_y = -dx * s + dy * c;  // 左距
    return [CAR_CX - local_y * MAP_SCALE, CAR_CY - local_x * MAP_SCALE];
  }

  function applyMap(data){
    if (!data || !data.pose) return;
    const pose = data.pose;
    $('#schemPose').textContent = `X:${Math.round(pose.x)} Y:${Math.round(pose.y)} θ:${pose.theta_deg}°`;

    // 轨迹
    const trail = document.getElementById('worldTrail');
    if (trail){
      let pts = '';
      (data.pose_history || []).forEach(p => {
        const [sx, sy] = worldToSvg(p.x, p.y, pose);
        pts += `${sx.toFixed(1)},${sy.toFixed(1)} `;
      });
      pts += `${CAR_CX},${CAR_CY}`;
      trail.setAttribute('points', pts);
    }

    // Occupancy cells
    const cell_mm = data.cell_mm || 100;
    const cell_px = cell_mm * MAP_SCALE;
    const occ = document.getElementById('worldOcc');
    if (occ){
      let maxN = 1;
      (data.cells || []).forEach(c => { if (c[2] > maxN) maxN = c[2]; });
      const parts = [];
      (data.cells || []).forEach(c => {
        const [wx, wy, n] = c;
        const [sx, sy] = worldToSvg(wx + cell_mm/2, wy + cell_mm/2, pose);
        if (sx < -20 || sx > 420 || sy < -20 || sy > 340) return;
        const alpha = Math.min(0.92, 0.28 + (n / maxN) * 0.6);
        parts.push(`<rect x="${(sx-cell_px/2).toFixed(1)}" y="${(sy-cell_px/2).toFixed(1)}" width="${cell_px.toFixed(1)}" height="${cell_px.toFixed(1)}" fill="#ff8a2e" opacity="${alpha.toFixed(2)}"/>`);
      });
      occ.innerHTML = parts.join('');
    }

    // Recent hits (闪烁红点,3 秒 fade)
    const hits = document.getElementById('worldHits');
    if (hits){
      const now = Date.now();
      const parts = [];
      (data.recent_hits || []).forEach(h => {
        const age = (now - h.t) / 1000;
        if (age > 3) return;
        const [sx, sy] = worldToSvg(h.x, h.y, pose);
        if (sx < -10 || sx > 410 || sy < -10 || sy > 330) return;
        const alpha = Math.max(0, 1 - age / 3);
        parts.push(`<circle cx="${sx.toFixed(1)}" cy="${sy.toFixed(1)}" r="2.2" fill="#ff3a2e" opacity="${alpha.toFixed(2)}"/>`);
      });
      hits.innerHTML = parts.join('');
    }

    // Recording trail overlay:回放时叠灰虚线(原录)+ 亮琥珀(回放实际)
    const recTrail = document.getElementById('recordedTrail');
    const repTrail = document.getElementById('replayTrail');
    if (recTrail && repTrail){
      if (recState.playing && recState.playback){
        const drawPolyline = (pts, el) => {
          let s = '';
          pts.forEach(p => {
            if (p && p.x != null){
              const [sx, sy] = worldToSvg(p.x, p.y, pose);
              s += `${sx.toFixed(1)},${sy.toFixed(1)} `;
            }
          });
          el.setAttribute('points', s);
        };
        drawPolyline(recState.playback.trail_recorded || [], recTrail);
        drawPolyline(recState.playback.trail_replay || [], repTrail);
      } else {
        recTrail.setAttribute('points', '');
        repTrail.setAttribute('points', '');
      }
    }

    // Origin marker(起点 0,0)— 在视野内才显示
    const origin = document.getElementById('originMarker');
    if (origin){
      const [ox, oy] = worldToSvg(0, 0, pose);
      if (ox > 5 && ox < 395 && oy > 5 && oy < 315){
        origin.style.display = '';
        origin.setAttribute('transform', `translate(${ox.toFixed(1)} ${oy.toFixed(1)})`);
      } else {
        origin.style.display = 'none';
      }
    }
  }

  $('#btnMapReset').addEventListener('click', async () => {
    try {
      await fetch('/api/map/reset', {method: 'POST'});
      const r = await fetch('/api/map/state');
      if (r.ok) applyMap(await r.json());
    } catch(e){}
  });

  // RTS RESET ESP32 按钮(deadlock banner 上的手动按钮)
  const btnRtsReset = document.getElementById('btnTryRtsReset');
  if (btnRtsReset){
    btnRtsReset.addEventListener('click', async () => {
      btnRtsReset.disabled = true;
      btnRtsReset.textContent = '⟳ resetting...';
      try {
        const r = await fetch('/api/system/esp32_reset', {method: 'POST'});
        const j = await r.json();
        if (r.ok) {
          btnRtsReset.textContent = '⟳ booting... wait 10s';
          setTimeout(() => {
            btnRtsReset.disabled = false;
            btnRtsReset.textContent = '⟳ RTS RESET';
          }, 12000);
        } else {
          btnRtsReset.textContent = '⟳ failed (' + j.detail + ')';
          setTimeout(() => {
            btnRtsReset.disabled = false;
            btnRtsReset.textContent = '⟳ RTS RESET';
          }, 5000);
        }
      } catch(e) {
        btnRtsReset.disabled = false;
        btnRtsReset.textContent = '⟳ RTS RESET';
      }
    });
  }

  // RESTART 服务按钮
  $('#btnRestart').addEventListener('click', async () => {
    if (!confirm('Restart robot-console service?\nWebSocket 会短暂断开,几秒后自动重连。')) return;
    try {
      await fetch('/api/system/restart', {method: 'POST'});
    } catch(e){}
    // 显示重启中提示
    let banner = document.getElementById('restartBanner');
    if (!banner){
      banner = document.createElement('div');
      banner.id = 'restartBanner';
      banner.style.cssText = 'position:fixed;top:0;left:0;right:0;padding:14px;background:rgba(255,58,46,.9);color:#0b0907;'
        + 'text-align:center;font-family:var(--font-mono);font-weight:700;letter-spacing:.2em;z-index:9999;font-size:12px;';
      banner.textContent = '⟳ RESTARTING ROBOT-CONSOLE … RECONNECTING IN ~5s';
      document.body.appendChild(banner);
    }
    // 5 秒后自动隐藏(WS 会自己重连刷新状态)
    setTimeout(() => { try{ banner.remove(); }catch(e){} }, 8000);
  });

  // 页面初次加载就拉一次地图状态(不必等下次 200ms WS push)
  fetch('/api/map/state').then(r => r.ok ? r.json() : null).then(d => { if (d) applyMap(d); }).catch(() => {});

  // ============ HUD overlay (mode badge + obstacle alert) ============
  function updateHudFromUltra(u){
    const anyDanger = [1,2,3].some(id => u[id] && u[id].has_obstacle);
    const alertEl = document.getElementById('hudAlert');
    if (alertEl) alertEl.setAttribute('opacity', anyDanger ? '1' : '0');
  }
  function updateHudMode(em){
    let mode = 'STANDBY';
    if (em) mode = '! E-STOP !';
    else if (modeObs) mode = 'AUTO-AVOID';
    else if (modeUltra) mode = 'TELEMETRY';
    const t = document.getElementById('hudModeText');
    if (t) t.textContent = mode;
    const rect = document.querySelector('#hudMode rect');
    if (rect){
      rect.setAttribute('stroke', em ? '#ff3a2e' : (modeObs || modeUltra ? '#7fff5a' : '#6fd685'));
    }
    if (t) t.setAttribute('fill', em ? '#ff3a2e' : (modeObs || modeUltra ? '#7fff5a' : '#6fd685'));
  }

  // ============ Recording ============
  let recState = {recording:false, playing:false, event_count:0, duration_s:0, elapsed_s:0, playback:null};
  function applyRec(d){
    recState = d || recState;
    const btnRec = $('#btnRecToggle');
    const btnClear = $('#btnRecClear');
    const btnPlay = $('#btnRecPlayFwd');
    const btnRev = $('#btnRecPlayRev');
    const btnStop = $('#btnRecStopPlay');
    const stEl = $('#recPanelState');
    const metaEl = $('#recPanelMeta');
    const progRow = $('#recProgRow');
    const progFill = $('#recProgFill');
    const progMeta = $('#recProgMeta');
    const curEl = $('#recCurrent');
    const curTxt = $('#recCurrentText');
    const calBox = $('#recCalDiff');

    // 状态文字 + REC 按钮形态
    if (d.recording){
      stEl.textContent = `RECORDING · ${d.elapsed_s.toFixed(1)}s`;
      stEl.style.color = '#ff3a2e';
      btnRec.textContent = '■ STOP REC';
      btnRec.classList.add('recording');
    } else if (d.playing){
      const pb = d.playback || {};
      const dirTxt = pb.direction === 'reverse' ? '◀ REV' : '▶ FWD';
      stEl.textContent = `PLAYBACK · ${dirTxt} ${pb.speed?.toFixed(2)}×`;
      stEl.style.color = '#5af0ff';
      btnRec.textContent = '● REC';
      btnRec.classList.remove('recording');
    } else {
      stEl.textContent = d.event_count > 0 ? 'READY · IDLE' : 'IDLE';
      stEl.style.color = '';
      btnRec.textContent = '● REC';
      btnRec.classList.remove('recording');
    }
    metaEl.textContent = `${d.event_count} · ${d.duration_s.toFixed(1)}s`;

    // 按钮 disabled 状态
    const idle = !d.recording && !d.playing;
    const hasRec = d.event_count > 0;
    btnRec.disabled = d.playing;
    btnClear.disabled = !idle || !hasRec;
    btnPlay.disabled = !idle || !hasRec;
    btnRev.disabled = !idle || !hasRec;
    btnStop.disabled = !d.playing;

    // 回放进度条 + 当前指令
    if (d.playing && d.playback){
      const pb = d.playback;
      const pct = pb.duration > 0 ? Math.min(100, 100 * pb.elapsed / pb.duration) : 0;
      progRow.style.display = '';
      progFill.style.width = pct.toFixed(1) + '%';
      progMeta.textContent = `${pb.elapsed.toFixed(1)}/${pb.duration.toFixed(1)}s · ${pb.idx}/${pb.total}`;
      if (pb.current){
        curEl.style.display = '';
        const kind = pb.current.kind || '';
        const act = pb.current.action || '';
        curTxt.textContent = `${kind.toUpperCase()} · ${act.toUpperCase()}`;
      } else {
        curEl.style.display = 'none';
      }
      // 校准对比
      if (pb.calibrate && pb.ultra_recorded && pb.ultra_live){
        calBox.style.display = '';
        [1,2,3].forEach(id => {
          const r = pb.ultra_recorded[id];
          const l = pb.ultra_live[id];
          const diff = (pb.ultra_diff_mm || {})[id];
          $(`#recCalRec${id}`).textContent = r != null ? r : '—';
          $(`#recCalLive${id}`).textContent = l != null ? l : '—';
          const dEl = $(`#recCalDiff${id}`);
          if (diff != null){
            dEl.textContent = (diff >= 0 ? '+' : '') + diff;
            dEl.style.color = Math.abs(diff) > 150 ? '#ff3a2e' : (Math.abs(diff) > 50 ? '#6fd685' : '#7fff5a');
          } else { dEl.textContent = '—'; dEl.style.color = ''; }
        });
      } else {
        calBox.style.display = 'none';
      }
    } else {
      progRow.style.display = 'none';
      curEl.style.display = 'none';
      calBox.style.display = 'none';
    }
  }

  // Rec button handlers
  $('#btnRecToggle').addEventListener('click', async () => {
    try {
      if (recState.recording){ await fetch('/api/rec/stop', {method:'POST'}); }
      else { await fetch('/api/rec/start', {method:'POST'}); }
    } catch(e){}
  });
  $('#btnRecClear').addEventListener('click', async () => {
    try { await fetch('/api/rec/clear', {method:'POST'}); } catch(e){}
  });
  $('#btnRecPlayFwd').addEventListener('click', async () => {
    const sp = $('#recSpeed').value; const cal = $('#recCalibrate').checked ? 1 : 0;
    try { await fetch(`/api/rec/play?direction=forward&speed=${sp}&calibrate=${cal}`, {method:'POST'}); } catch(e){}
  });
  $('#btnRecPlayRev').addEventListener('click', async () => {
    const sp = $('#recSpeed').value; const cal = $('#recCalibrate').checked ? 1 : 0;
    try { await fetch(`/api/rec/play?direction=reverse&speed=${sp}&calibrate=${cal}`, {method:'POST'}); } catch(e){}
  });
  $('#btnRecStopPlay').addEventListener('click', async () => {
    try { await fetch('/api/rec/stop_playback', {method:'POST'}); } catch(e){}
  });
  $('#recSpeed').addEventListener('input', ev => {
    $('#recSpeedVal').textContent = parseFloat(ev.target.value).toFixed(2) + '×';
  });
  // BACKWARD_RATIO 校准
  $('#backRatio').addEventListener('input', ev => {
    $('#backRatioVal').textContent = parseFloat(ev.target.value).toFixed(2);
  });
  $('#backRatio').addEventListener('change', async ev => {
    const v = parseFloat(ev.target.value);
    try {
      await fetch('/api/map/config', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({backward_ratio: v})});
    } catch(e){}
  });
  // 启动时拉一次 map config 同步初值
  (async () => {
    try {
      const r = await fetch('/api/map/state'); const d = await r.json();
      if (d && d.config && d.config.backward_ratio != null){
        $('#backRatio').value = d.config.backward_ratio;
        $('#backRatioVal').textContent = parseFloat(d.config.backward_ratio).toFixed(2);
      }
    } catch(e){}
  })();

  function applyUltra(u){
    const MAX = 4000;
    [1,2,3].forEach(id => {
      const data = u[id];
      const card = document.querySelector(`.ureading[data-id="${id}"]`);
      if (!card) return;
      const valEl = card.querySelector('.ureading-val');
      const bar = card.querySelector('.ureading-bar > div');
      const sonar = document.getElementById('sonar' + id);
      if (!data){
        valEl.textContent='----'; bar.style.width='0%';
        card.classList.remove('danger'); valEl.classList.remove('danger');
        if (sonar) sonar.setAttribute('opacity','0');
        return;
      }
      valEl.textContent = String(data.distance_mm).padStart(4,'0');
      bar.style.width = Math.min(100,(data.distance_mm/MAX)*100) + '%';
      const danger = !!data.has_obstacle;
      card.classList.toggle('danger', danger);
      valEl.classList.toggle('danger', danger);
      if (sonar){
        sonar.setAttribute('opacity','1');
        const len = Math.min(120, (data.distance_mm/MAX)*100 + 20);
        const half = len*0.55;
        const color = danger ? 'url(#sonarR)' : 'url(#sonarG)';
        const strokeColor = danger ? '#ff3a2e' : '#6fd685';
        sonar.innerHTML = `
          <path d="M 0 -40 L ${-half} ${-40-len} A ${len*1.1} ${len*1.1} 0 0 1 ${half} ${-40-len} Z"
                fill="${color}" stroke="${strokeColor}" stroke-width="0.5" opacity="0.7"/>
          <line x1="0" y1="-40" x2="0" y2="${-40-len-4}" stroke="${strokeColor}" stroke-width="0.5" opacity="0.8"/>
          <text x="0" y="${-44-len}" text-anchor="middle" font-size="11"
                fill="${strokeColor}" font-family="JetBrains Mono, ui-monospace, Menlo, monospace" font-weight="700">${data.distance_mm}</text>
        `;
      }
    });
    updateHudFromUltra(u);
  }

  // ============ YOLO control ============
  // 类别 id → 稳定颜色(避免每帧 random 闪烁)
  const yoloPalette = ['#7fff5a','#6fd685','#5af0ff','#ff5af0','#ff8a2e','#a5e8ff','#a3e6b0','#7affb0','#ff9a9a','#c19aff'];
  const yoloColor = id => yoloPalette[(id|0) % yoloPalette.length];

  function renderYoloBoxes(dets){
    const layer = document.getElementById('yoloBoxes');
    if (!layer) return;
    if (!dets || dets.length === 0){ layer.innerHTML = ''; return; }
    // SVG viewBox 是 0..400 x 0..300, preserveAspectRatio=none 让 SVG 拉伸到 cam-frame
    const VW = 400, VH = 300;
    let html = '';
    dets.forEach(d => {
      let x = d.x * VW;
      const y = d.y * VH;
      const w = d.w * VW;
      const h = d.h * VH;
      // REAR CAM 镜像模式:bbox 跟 cam-img 一起水平翻转(图像里物体位置变了)
      if (rearMode) x = VW - x - w;
      const c = yoloColor(d.cls);
      const text = `${d.label} ${Math.round(d.conf * 100)}`;
      const tw = Math.min(VW - x, text.length * 6 + 8);
      html += `<rect class="yolo-box-rect" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" stroke="${c}"/>`;
      html += `<rect x="${x.toFixed(1)}" y="${(y-11).toFixed(1)}" width="${tw}" height="11" fill="${c}" opacity="0.92"/>`;
      html += `<text class="yolo-box-label" x="${(x+3).toFixed(1)}" y="${(y-2).toFixed(1)}">${text}</text>`;
    });
    layer.innerHTML = html;
  }

  async function yoloPost(body){
    try{
      const r = await fetch('/api/yolo/config', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(body)
      });
      return r.ok;
    }catch(e){ return false; }
  }

  let yoloEnabled = false;
  const yoloBtn = $('#btnYolo');
  const yoloConfSlider = $('#yoloConf'),  yoloConfVal  = $('#yoloConfVal');
  const yoloImgszSlider = $('#yoloImgsz'), yoloImgszVal = $('#yoloImgszVal');
  const yoloRateSlider = $('#yoloRate'),  yoloRateVal  = $('#yoloRateVal');

  yoloBtn.addEventListener('click', async () => {
    yoloEnabled = !yoloEnabled;
    yoloBtn.classList.toggle('on', yoloEnabled);
    $('#yoloBadge').textContent = yoloEnabled ? 'ON' : 'OFF';
    await yoloPost({enabled: yoloEnabled});
    if (!yoloEnabled){
      renderYoloBoxes([]);
      $('#yoloStat').textContent = '— ms · 0';
      // 防止 await POST 期间残留 WS event 把 bbox 画回去 — 延迟再清一次
      setTimeout(() => renderYoloBoxes([]), 300);
      setTimeout(() => renderYoloBoxes([]), 1000);
    }
  });
  yoloConfSlider.addEventListener('input', e => yoloConfVal.textContent = parseFloat(e.target.value).toFixed(2));
  yoloConfSlider.addEventListener('change', e => yoloPost({conf: parseFloat(e.target.value)}));
  yoloImgszSlider.addEventListener('input', e => yoloImgszVal.textContent = e.target.value);
  yoloImgszSlider.addEventListener('change', e => yoloPost({imgsz: parseInt(e.target.value)}));
  yoloRateSlider.addEventListener('input', e => yoloRateVal.textContent = parseFloat(e.target.value).toFixed(1) + 's');
  yoloRateSlider.addEventListener('change', e => yoloPost({min_interval: parseFloat(e.target.value)}));

  // 类别过滤
  const yoloFilterInput = $('#yoloFilter');
  const yoloFilterEcho = $('#yoloFilterEcho');
  let yoloNameToId = {};   // {"person":0, ...}
  let yoloIdToName = {};

  function refreshFilterEcho(ids){
    if (!ids || ids.length === 0){
      yoloFilterEcho.textContent = 'filter: all classes';
      yoloFilterEcho.style.color = 'var(--text-mute)';
      return;
    }
    const names = ids.map(id => yoloIdToName[id] || ('#' + id));
    yoloFilterEcho.textContent = 'filter: ' + names.join(', ');
    yoloFilterEcho.style.color = 'var(--amber-l)';
  }

  function parseFilterIds(){
    const txt = yoloFilterInput.value.trim();
    if (!txt) return [];
    return txt.split(',').map(s => s.trim().toLowerCase()).filter(Boolean)
      .map(s => /^\d+$/.test(s) ? parseInt(s) : yoloNameToId[s])
      .filter(x => Number.isInteger(x));
  }

  let yoloFilterDebounce = null;
  function applyFilter(){
    const ids = parseFilterIds();
    refreshFilterEcho(ids);
    yoloPost({classes: ids.length ? ids : null});
  }
  yoloFilterInput.addEventListener('input', () => {
    clearTimeout(yoloFilterDebounce);
    yoloFilterDebounce = setTimeout(applyFilter, 250);
  });
  yoloFilterInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); clearTimeout(yoloFilterDebounce); applyFilter(); yoloFilterInput.blur(); }
    if (e.key === 'Escape') { yoloFilterInput.value = ''; clearTimeout(yoloFilterDebounce); applyFilter(); }
  });

  // 拉一次状态同步初始 UI(服务可能已经 enabled = true 持久化的状态)
  fetch('/api/yolo/status').then(r => r.json()).then(s => {
    if (!s.available) {
      yoloBtn.disabled = true;
      yoloBtn.style.opacity = '0.4';
      $('#yoloBadge').textContent = 'N/A';
      return;
    }
    yoloEnabled = !!s.enabled;
    yoloBtn.classList.toggle('on', yoloEnabled);
    $('#yoloBadge').textContent = yoloEnabled ? 'ON' : 'OFF';
    if (s.config){
      yoloConfSlider.value = s.config.conf;
      yoloConfVal.textContent = parseFloat(s.config.conf).toFixed(2);
      yoloImgszSlider.value = s.config.imgsz;
      yoloImgszVal.textContent = s.config.imgsz;
      yoloRateSlider.value = s.config.min_interval;
      yoloRateVal.textContent = parseFloat(s.config.min_interval).toFixed(1) + 's';
    }
    // 填充 datalist + 构建映射
    if (s.model_classes){
      const dl = $('#yoloClassList');
      dl.innerHTML = s.model_classes.map(c => `<option value="${c.name}">`).join('');
      s.model_classes.forEach(c => {
        yoloNameToId[c.name.toLowerCase()] = c.id;
        yoloIdToName[c.id] = c.name;
      });
    }
    // 持久化的 classes 反显
    if (s.config && Array.isArray(s.config.classes) && s.config.classes.length){
      yoloFilterInput.value = s.config.classes.map(id => yoloIdToName[id] || id).join(',');
      refreshFilterEcho(s.config.classes);
    } else {
      refreshFilterEcho([]);
    }
  }).catch(() => {});

  // ============ ARM tab (机械臂) ============
  $$('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      $$('.tab-btn').forEach(b => b.classList.toggle('active', b === btn));
      $$('.tab-content').forEach(c => {
        c.style.display = c.id === ('tab-' + btn.dataset.tab) ? '' : 'none';
      });
    });
  });

  const ARM_STEP_M = 0.01;
  $$('[data-arm-nudge]').forEach(el => {
    el.addEventListener('click', async (ev) => {
      ev.preventDefault();
      const dir = el.dataset.armNudge;
      const body = {dx: 0, dy: 0, dz: 0};
      if (dir === 'x+') body.dx = ARM_STEP_M;
      else if (dir === 'x-') body.dx = -ARM_STEP_M;
      else if (dir === 'y+') body.dy = ARM_STEP_M;
      else if (dir === 'y-') body.dy = -ARM_STEP_M;
      else if (dir === 'z+') body.dz = ARM_STEP_M;
      else if (dir === 'z-') body.dz = -ARM_STEP_M;
      el.classList.add('active');
      try {
        await fetch('/api/arm/nudge_cartesian', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify(body),
        });
      } catch(e){}
      setTimeout(() => el.classList.remove('active'), 200);
    });
  });

  $$('[data-arm-act]').forEach(el => {
    el.addEventListener('click', async () => {
      const a = el.dataset.armAct;
      let url, body;
      if (a === 'home') { url = '/api/arm/home'; body = {}; }
      else if (a === 'gripper-open')  { url = '/api/arm/gripper'; body = {delta_deg:  5}; }
      else if (a === 'gripper-close') { url = '/api/arm/gripper'; body = {delta_deg: -5}; }
      if (!url) return;
      try {
        await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                          body: JSON.stringify(body)});
      } catch(e){}
    });
  });

  async function loadArmStatus(){
    try {
      const r = await fetch('/api/arm/status');
      const s = await r.json();
      const label = $('#armStatusLabel');
      if (!s.available){
        if (label) label.textContent = 'NOT INSTALLED';
        return;
      }
      if (!s.connected){
        if (label) label.textContent = (s.reason || s.error || 'disconnected').toUpperCase().slice(0, 20);
        return;
      }
      if (label) label.textContent = 'CONNECTED';
      if (s.cartesian_position_m){
        const p = s.cartesian_position_m;
        $('#armX').textContent = (p.x * 1000).toFixed(0);
        $('#armY').textContent = (p.y * 1000).toFixed(0);
        $('#armZ').textContent = (p.z * 1000).toFixed(0);
      }
      if (s.present_positions_deg){
        const grid = $('#armJointGrid');
        // joint 数量变化时(servo 上下线、reset)强制 rebuild
        const curNames = Object.keys(s.present_positions_deg).join(',');
        if (grid && grid.dataset.builtFor !== curNames){
          grid.dataset.builtFor = curNames;
          let html = '';
          const metaMap = window.armMetaMap || {};
          for (const name of Object.keys(s.present_positions_deg)){
            const shortName = name.replace('joint_','J').toUpperCase().replace('GRIPPER','GRIP');
            const meta = metaMap[name] || {};
            const desc = meta.description || '';
            const calibrated = meta.calibrated !== false;
            const minD = meta.min_deg;
            const maxD = meta.max_deg;
            const limitTxt = (minD != null && maxD != null) ? `${minD}~${maxD}°` : '— ~ —';
            const warnCls = calibrated ? '' : 'uncalibrated';
            const minAttr = (minD != null) ? `min="${minD}"` : '';
            const maxAttr = (maxD != null) ? `max="${maxD}"` : '';
            const disabledAttr = calibrated ? '' : 'disabled';
            const inpTitle = calibrated ? `范围 ${limitTxt}` : '⚠ 未校准 limit · 暂禁输入 · 用 nudge ±2° 试探确认范围后再开';
            const descTitle = calibrated ? desc : desc + '  · ⚠ 未校准';
            html += `
              <div class="arm-joint-row ${warnCls}">
                <div class="arm-joint-head">
                  <span class="arm-joint-name">${shortName}</span>
                  <span class="arm-joint-desc" title="${descTitle}">${desc || '—'}</span>
                  <span class="arm-joint-limit" title="config 配置 limit">${limitTxt}${calibrated ? '' : ' ⚠'}</span>
                  <span class="arm-obs" data-joint-obs="${name}" title="浏览器记录的实测 min~max(LocalStorage)">— ~ —°</span>
                </div>
                <div class="arm-joint-ctrl">
                  <span class="arm-joint-val" data-joint="${name}">—</span>
                  <input class="arm-joint-input" data-joint-input="${name}" type="number" step="0.5" placeholder="deg"
                    ${minAttr} ${maxAttr} ${disabledAttr} title="${inpTitle}">
                  <button class="btn arm-nudge" data-joint-nudge="${name}" data-delta="-2" title="-2°">−</button>
                  <button class="btn arm-nudge" data-joint-nudge="${name}" data-delta="2" title="+2°">+</button>
                </div>
              </div>`;
          }
          grid.innerHTML = html;
          grid.querySelectorAll('[data-joint-nudge]').forEach(b => {
            b.addEventListener('click', async () => {
              if (b.disabled) return;
              b.disabled = true;
              b.classList.add('arm-nudge-pending');
              const body = JSON.stringify({joint: b.dataset.jointNudge, delta_deg: parseFloat(b.dataset.delta)});
              try {
                const r = await fetch('/api/arm/nudge_joint', {
                  method:'POST', headers:{'Content-Type':'application/json'}, body,
                });
                b.classList.remove('arm-nudge-pending');
                if (r.ok){
                  b.classList.add('arm-nudge-ok');
                  setTimeout(() => b.classList.remove('arm-nudge-ok'), 300);
                } else {
                  let detail = '';
                  try { detail = (await r.json()).detail || ''; } catch{}
                  b.classList.add('arm-nudge-err');
                  b.title = `${r.status} ${detail}`;
                  console.error('nudge fail', b.dataset.jointNudge, b.dataset.delta, r.status, detail);
                  setTimeout(() => b.classList.remove('arm-nudge-err'), 1500);
                }
              } catch(e){
                b.classList.remove('arm-nudge-pending');
                b.classList.add('arm-nudge-err');
                console.error('nudge exception', e);
                setTimeout(() => b.classList.remove('arm-nudge-err'), 1500);
              }
              // 防 spam:200ms 内不让连点(controller arm_lock 串行,连点会丢)
              setTimeout(() => { b.disabled = false; }, 200);
            });
          });
          // 绝对角度输入:Enter / blur 提交 + client-side limit check
          grid.querySelectorAll('[data-joint-input]').forEach(inp => {
            const minD = inp.min !== '' ? parseFloat(inp.min) : null;
            const maxD = inp.max !== '' ? parseFloat(inp.max) : null;
            const checkLimit = (v) => {
              if (minD != null && v < minD) return `≤ ${minD}°`;
              if (maxD != null && v > maxD) return `≥ ${maxD}°`;
              return null;
            };
            const send = async () => {
              const v = parseFloat(inp.value);
              if (isNaN(v)) return;
              const lim = checkLimit(v);
              if (lim){
                inp.style.borderColor = '#ff3a2e';
                inp.style.background = 'rgba(255,58,46,0.15)';
                inp.title = `⛔ 越界:${v}° ${lim} · 请在 ${minD}~${maxD}° 内`;
                setTimeout(() => { inp.style.background = '#1a1610'; inp.style.borderColor = ''; }, 2000);
                return;
              }
              inp.style.borderColor = 'var(--amber)';
              try {
                const r = await fetch('/api/arm/move_joint', {
                  method:'POST', headers:{'Content-Type':'application/json'},
                  body: JSON.stringify({joint: inp.dataset.jointInput, target_deg: v}),
                });
                inp.style.borderColor = r.ok ? 'var(--green)' : '#ff3a2e';
                if (!r.ok){ try{ const j = await r.json(); inp.title = j.detail || j.error || 'err'; }catch{} }
              } catch(e){ inp.style.borderColor = '#ff3a2e'; }
              setTimeout(() => { inp.style.borderColor = ''; }, 800);
            };
            inp.addEventListener('input', () => {
              const v = parseFloat(inp.value);
              if (isNaN(v)) return;
              const lim = checkLimit(v);
              inp.style.color = lim ? '#ff7a6e' : 'var(--cyan)';
            });
            // 滚轮 = 让页面正常滚,不调整数值(默认 number input 滚轮改值很烦人)
            inp.addEventListener('wheel', ev => { inp.blur(); }, {passive:true});
            inp.addEventListener('keydown', ev => { if (ev.key === 'Enter'){ ev.preventDefault(); send(); inp.blur(); }});
            inp.addEventListener('blur', () => { if (inp.value !== '') send(); });
            // 双击输入框 = 用当前角度填充
            inp.addEventListener('dblclick', () => {
              const cur = grid.querySelector(`.arm-joint-val[data-joint="${inp.dataset.jointInput}"]`);
              if (cur) inp.value = parseFloat(cur.textContent).toFixed(0);
            });
          });
        }
        // 更新 joint values + 实测 observed min/max
        if (grid){
          for (const [name, deg] of Object.entries(s.present_positions_deg)){
            const el = grid.querySelector(`[data-joint="${name}"]`);
            if (el) el.textContent = deg.toFixed(1) + '°';
            updateObserved(name, deg);
          }
        }
      }
    } catch(e){}
  }
  async function loadArmMeta(){
    try {
      const r = await fetch('/api/arm/meta');
      if (!r.ok) return;
      const d = await r.json();
      window.armMetaMap = {};
      (d.joints || []).forEach(j => { window.armMetaMap[j.name] = j; });
    } catch(e){}
  }

  // ============ Observed min/max (实测 limit 校准工具) ============
  const OBS_KEY = 'armJointObserved';
  window.armObserved = JSON.parse(localStorage.getItem(OBS_KEY) || '{}');
  function fmtObs(o){ return o ? `${o.min.toFixed(1)} ~ ${o.max.toFixed(1)}°` : '— ~ —°'; }
  function renderObsUI(){
    document.querySelectorAll('.arm-obs').forEach(el => {
      el.textContent = fmtObs(window.armObserved[el.dataset.jointObs]);
    });
  }
  function updateObserved(name, deg){
    if (isNaN(deg) || !isFinite(deg)) return;
    const cur = window.armObserved[name];
    let changed = false;
    if (!cur){
      window.armObserved[name] = {min: deg, max: deg};
      changed = true;
    } else {
      if (deg < cur.min){ cur.min = deg; changed = true; }
      if (deg > cur.max){ cur.max = deg; changed = true; }
    }
    if (changed){
      localStorage.setItem(OBS_KEY, JSON.stringify(window.armObserved));
      const el = document.querySelector(`.arm-obs[data-joint-obs="${name}"]`);
      if (el) el.textContent = fmtObs(window.armObserved[name]);
    }
  }
  // RESET ARM — 脱限位救援
  const btnArmReset = document.getElementById('btnArmReset');
  if (btnArmReset){
    btnArmReset.addEventListener('click', async () => {
      if (!confirm('重置 arm controller?\n\n这会 disconnect 当前 lerobot 连接然后重新 from_json + connect。\nservo 物理状态(multi-turn 累计计数 / 过载错误)不会被清,要清那个必须断 servo 电源 5 秒再上电。\n\n现在的 limit 已经放到 ±360,reset 后能正常 move。')) return;
      btnArmReset.disabled = true;
      btnArmReset.textContent = '🔄 RESETTING...';
      try {
        const r = await fetch('/api/arm/reset', {method:'POST'});
        const d = await r.json();
        if (r.ok){
          btnArmReset.textContent = '✓ RESET OK';
          console.log('arm reset positions:', d.positions);
          setTimeout(() => { btnArmReset.textContent = '🔄 RESET ARM'; btnArmReset.disabled = false; }, 2000);
        } else {
          btnArmReset.textContent = '✗ ' + (d.detail || 'ERR');
          setTimeout(() => { btnArmReset.textContent = '🔄 RESET ARM'; btnArmReset.disabled = false; }, 3000);
        }
      } catch(e){
        btnArmReset.textContent = '✗ ERR';
        setTimeout(() => { btnArmReset.textContent = '🔄 RESET ARM'; btnArmReset.disabled = false; }, 3000);
      }
    });
  }

  // Export / Reset handlers
  function bindObsButtons(){
    const exp = document.getElementById('btnExportObs');
    const rst = document.getElementById('btnResetObs');
    if (exp && !exp.dataset.bound){
      exp.dataset.bound = '1';
      exp.addEventListener('click', async () => {
        const out = {};
        Object.entries(window.armObserved).forEach(([name, o]) => {
          out[name] = {min_deg: Math.round(o.min * 10) / 10, max_deg: Math.round(o.max * 10) / 10};
        });
        const text = JSON.stringify(out, null, 2);
        try { await navigator.clipboard.writeText(text); } catch(e){}
        // 弹窗显示让用户能看 + 复制(clipboard 不一定 work)
        const w = window.open('', '_blank', 'width=500,height=600');
        if (w){
          w.document.write(`<pre style="font-family:JetBrains Mono,monospace;font-size:13px;padding:16px;background:#1a1610;color:#6fd685">${text.replace(/</g,'&lt;')}</pre>`);
          w.document.title = 'arm joint observed min/max';
        } else {
          alert('已复制到剪贴板:\n\n' + text);
        }
      });
    }
    if (rst && !rst.dataset.bound){
      rst.dataset.bound = '1';
      rst.addEventListener('click', () => {
        if (!confirm('清空所有 joint 的浏览器实测 min/max 记录?')) return;
        window.armObserved = {};
        localStorage.removeItem(OBS_KEY);
        document.querySelectorAll('.arm-obs').forEach(el => el.textContent = '— ~ —°');
      });
    }
  }

  loadArmMeta().then(() => {
    loadArmStatus();
    setInterval(loadArmStatus, 3000);
    // grid 是在 loadArmStatus 第一次执行时构建,稍等再 bind + render
    setTimeout(() => { renderObsUI(); bindObsButtons(); }, 500);
  });

  // ============ Arm Preset Slots + Loop ============
  const armPresetSpeed = $('#armPresetSpeed');
  const armPresetSpeedVal = $('#armPresetSpeedVal');
  if (armPresetSpeed){
    armPresetSpeed.addEventListener('input', () => {
      armPresetSpeedVal.textContent = armPresetSpeed.value + '°/s';
    });
  }
  const armLoopInterval = $('#armLoopInterval');
  const armLoopIntervalVal = $('#armLoopIntervalVal');
  if (armLoopInterval){
    armLoopInterval.addEventListener('input', () => {
      armLoopIntervalVal.textContent = armLoopInterval.value + 'ms';
    });
  }
  async function loadPresets(){
    try {
      const r = await fetch('/api/arm/presets');
      const d = await r.json();
      (d.slots || []).forEach((slot, i) => {
        const btn = document.querySelector(`[data-preset-move="${i}"]`);
        if (!btn) return;
        if (slot){
          btn.textContent = slot.label || `SLOT ${i+1}`;
          btn.classList.add('on');
          btn.title = `${Object.keys(slot.positions_deg||{}).length} joints saved · click 移动 · 长按重命名`;
        } else {
          btn.textContent = `SLOT ${i+1}`;
          btn.classList.remove('on');
          btn.title = '空 · 先保存才能移动';
        }
      });
    } catch(e){}
  }
  // click move
  document.querySelectorAll('[data-preset-move]').forEach(b => {
    b.addEventListener('click', async () => {
      if (!b.classList.contains('on')) {
        b.style.borderColor = 'var(--err)';
        setTimeout(() => { b.style.borderColor = ''; }, 600);
        return;
      }
      const idx = b.dataset.presetMove;
      const speed = armPresetSpeed?.value || 180;
      b.style.background = 'rgba(111,214,133,.4)';
      try {
        const r = await fetch(`/api/arm/preset/${idx}/move?speed_deg_s=${speed}`, {method:'POST'});
        b.style.background = r.ok ? 'rgba(111,214,133,.2)' : 'rgba(255,90,106,.3)';
      } catch(e){ b.style.background = 'rgba(255,90,106,.3)'; }
      setTimeout(() => { b.style.background = ''; }, 800);
    });
  });
  // click save (with confirm + optional label)
  document.querySelectorAll('[data-preset-save]').forEach(b => {
    b.addEventListener('click', async () => {
      const idx = b.dataset.presetSave;
      const cur = document.querySelector(`[data-preset-move="${idx}"]`);
      const has = cur?.classList.contains('on');
      const label = prompt(has ? `覆盖 Slot ${parseInt(idx)+1}\n输入新名字(空=保留旧名):` : `保存到 Slot ${parseInt(idx)+1}\n名字(留空用默认):`, has ? cur.textContent : `Slot ${parseInt(idx)+1}`);
      if (label === null) return;  // cancel
      try {
        await fetch(`/api/arm/preset/${idx}/save`, {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({label: label || `Slot ${parseInt(idx)+1}`})});
        await loadPresets();
      } catch(e){}
    });
  });
  // loop toggle
  let armLoopRunning = false;
  const btnLoop = $('#btnArmLoopStart');
  if (btnLoop){
    btnLoop.addEventListener('click', async () => {
      if (armLoopRunning){
        await fetch('/api/arm/preset_loop/stop', {method:'POST'});
        return;
      }
      const indices = [];
      document.querySelectorAll('[data-preset-move]').forEach(b => {
        if (b.classList.contains('on')) indices.push(parseInt(b.dataset.presetMove));
      });
      if (indices.length < 2){ alert('至少 2 个 slot 已保存才能循环'); return; }
      const speed = parseFloat(armPresetSpeed?.value || 180);
      const interval_ms = parseInt(armLoopInterval?.value || 800);
      await fetch('/api/arm/preset_loop/start', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({indices, interval_ms, speed_deg_s: speed, cycles: 0})});
    });
  }
  // poll loop status to highlight cur_idx + toggle button text
  async function pollLoop(){
    try {
      const r = await fetch('/api/arm/preset_loop/status'); const d = await r.json();
      armLoopRunning = !!d.running;
      if (btnLoop){
        btnLoop.classList.toggle('on', armLoopRunning);
        btnLoop.textContent = armLoopRunning ? '■ STOP LOOP' : '▶ LOOP';
      }
      document.querySelectorAll('.preset-move').forEach(el => {
        el.classList.toggle('cur', armLoopRunning && d.cur_idx !== null && parseInt(el.dataset.presetMove) === d.cur_idx);
      });
    } catch(e){}
  }
  loadPresets();
  setInterval(pollLoop, 500);

  // ARM torque toggle
  let armTorqueOn = false;
  const btnArmTorque = $('#btnArmTorque');
  if (btnArmTorque){
    btnArmTorque.addEventListener('click', async () => {
      const next = !armTorqueOn;
      btnArmTorque.disabled = true;
      try {
        const r = await fetch('/api/arm/torque', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({on: next})});
        if (r.ok){
          armTorqueOn = next;
          btnArmTorque.classList.toggle('on', armTorqueOn);
          $('#armTorqueBadge').textContent = armTorqueOn ? 'ON · LOCKED' : 'OFF';
        }
      } catch(e){}
      btnArmTorque.disabled = false;
    });
  }
  // SAVE HOME:把当前 6 个 joint 位置存为 home 目标
  const btnSaveHome = $('#btnSaveHome');
  if (btnSaveHome){
    btnSaveHome.addEventListener('click', async () => {
      if (!confirm('把当前所有 joint 位置存为新 HOME 目标?以后按 ⌂ HOME 会回到这里。')) return;
      btnSaveHome.disabled = true;
      btnSaveHome.textContent = 'SAVING...';
      try {
        const r = await fetch('/api/arm/save_home', {method:'POST'});
        const d = await r.json();
        if (r.ok){
          btnSaveHome.textContent = '✓ SAVED';
          setTimeout(() => { btnSaveHome.textContent = '💾 SAVE HOME'; btnSaveHome.disabled = false; }, 1500);
          console.log('home saved:', d.home_positions_deg);
        } else {
          btnSaveHome.textContent = '✗ ERR';
          setTimeout(() => { btnSaveHome.textContent = '💾 SAVE HOME'; btnSaveHome.disabled = false; }, 1500);
        }
      } catch(e){
        btnSaveHome.textContent = '✗ ERR';
        setTimeout(() => { btnSaveHome.textContent = '💾 SAVE HOME'; btnSaveHome.disabled = false; }, 1500);
      }
    });
  }
  // 把当前实际角度填到所有 joint input
  const btnArmReadback = $('#btnArmReadback');
  if (btnArmReadback){
    btnArmReadback.addEventListener('click', () => {
      document.querySelectorAll('[data-joint-input]').forEach(inp => {
        const grid = $('#armJointGrid');
        const cur = grid?.querySelector(`.arm-joint-val[data-joint="${inp.dataset.jointInput}"]`);
        if (cur) inp.value = parseFloat(cur.textContent).toFixed(0);
      });
    });
  }

  // ============ Log ============
  const MAX_LOG = 300;
  let logCount = 0;
  function addLog(entry){
    const box = $('#logBody');
    const line = document.createElement('div');
    const kind = entry.kind || 'esp32';
    line.className = `log-line kind-${kind}`;
    const d = new Date(entry.t);
    const ts = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}.${String(d.getMilliseconds()).padStart(3,'0')}`;
    line.innerHTML = `<span class="ts">${ts}</span><span class="kind">${kind}</span><span class="msg"></span>`;
    line.querySelector('.msg').textContent = entry.line;
    box.appendChild(line);
    while (box.children.length > MAX_LOG) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
    logCount++;
    $('#logCount').textContent = `${logCount} lines`;
  }
})();
</script>
</body>
</html>
"""


def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    import uvicorn
    ip = get_lan_ip()
    print(f"╔═══════════════════════════════════════════╗")
    print(f"║  ROBOT CONTROL UNIT // RPI-01             ║")
    print(f"╠═══════════════════════════════════════════╣")
    print(f"║  http://{ip}:8000")
    print(f"║  http://localhost:8000")
    print(f"╚═══════════════════════════════════════════╝")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
