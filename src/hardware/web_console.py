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

# ====== Prevent ESP32 reset when opening serial (preserves BT remote pairing) ======
# 默认 pyserial 打开串口会 assert DTR/RTS,在 ESP32+CH340 板上 = 触发 EN 复位
# → 蓝牙手柄连接丢失。这里把 DTR/RTS 先置 False 再 open,ESP32 不重启。
import serial as _pyserial
_orig_serial_init = _pyserial.Serial.__init__
def _patched_serial_init(self, *args, **kwargs):
    port = kwargs.get("port", args[0] if args else None)
    if port is None:
        _orig_serial_init(self, *args, **kwargs)
        return
    kwargs2 = {**kwargs, "port": None}
    if args and "port" not in kwargs:
        args = args[1:]
    _orig_serial_init(self, *args, **kwargs2)
    self.port = port
    try:
        self.dtr = False
        self.rts = False
    except Exception:
        pass
    self.open()
_pyserial.Serial.__init__ = _patched_serial_init

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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse


REC_DIR = "/home/pi/car_project/records"
SOUND_DIR = "/home/pi/car_project/sounds"

car: CarController | None = None
audio: AudioController | None = None
camera: "CameraStreamer | None" = None
yolo: "YOLOInferencer | None" = None
minimap: "MiniMap | None" = None
loop: asyncio.AbstractEventLoop | None = None

YOLO_MODELS_DIR = "/home/pi/yolo_env"
DEFAULT_YOLO_MODEL = "/home/pi/yolo_env/yolov8n.pt"
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
        FAIL_THRESHOLD = 15      # 连续这么多帧失败就触发重连
        RECONNECT_BACKOFF = 2.0   # 重连失败后等多久再试
        while self.running:
            t0 = time.time()
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
        elif a == "BACKWARD": vx_local = -v
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
                "config": {"k_linear": self.K_LINEAR, "k_angular": self.K_ANGULAR},
            }

    def configure(self, *, k_linear=None, k_angular=None):
        with self.lock:
            if k_linear is not None:
                self.K_LINEAR = max(0.001, min(1.0, float(k_linear)))
            if k_angular is not None:
                self.K_ANGULAR = max(0.00001, min(0.01, float(k_angular)))
        return self.snapshot()


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


def on_ps3_l1():
    push_log("PS3_L1_DOWN -> arm_action_1", "ps3")


def on_ps3_l2():
    push_log("PS3_L2_DOWN -> arm_action_2", "ps3")


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
        action = cmd_to_action.get(c, f"SEND({c})")
        state["last_action"] = action
        if action == "EMERGENCY_STOP":
            state["modes"]["emergency"] = True
        elif action != "STOP":
            state["modes"]["emergency"] = False
        push_log(f"tx: {action}", "cmd")
        bcast({"type": "state", "data": state})
        if minimap is not None:
            # 只把方向类 action 设为 current,STOP / 急停立即清零,其他保持
            if action in ("FORWARD","BACKWARD","LEFT","RIGHT","ROT_CW","ROT_CCW"):
                minimap.set_action(action)
            elif action in ("STOP", "EMERGENCY_STOP"):
                minimap.set_action(None)
        # 串口写带 1 次 retry(短瞬故障常见,长故障靠 serial watchdog)
        last_err = None
        for attempt in range(2):
            try:
                orig_send(c)
                last_err = None
                break
            except Exception as e:
                last_err = e
                time.sleep(0.05)
        if last_err is not None:
            push_log(f"send fail (2 attempts) {action}: {last_err}", "sys")

    car.send = wrapped_send


# ====================== Lifespan ======================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global car, audio, camera, yolo, minimap, loop
    loop = asyncio.get_running_loop()

    push_log("boot: initializing CarController", "sys")
    try:
        car = CarController(
            ultrasonic_callback=on_ultra,
            ps3_l1_callback=on_ps3_l1,
            ps3_l2_callback=on_ps3_l2,
        )
        install_event_hooks()
        push_log("boot: car ready", "sys")
        # 默认启用 ULTRA_REPORT,让超声波数据开箱即用。
        # 用户切到 OBSTACLE 模式时 setObs(true) 会先 ultra_off 再 obstacle_on(互斥已处理)。
        try:
            await asyncio.sleep(0.3)
            car.ultrasonic_report_on()
            push_log("boot: enabled ULTRA_REPORT by default", "sys")
        except Exception as e:
            push_log(f"boot: ultra_report auto-enable failed: {e}", "sys")
    except Exception as e:
        push_log(f"boot: car FAILED: {e}", "sys")
        car = None

    push_log("boot: initializing AudioController", "sys")
    try:
        audio = AudioController()
        push_log("boot: audio ready", "sys")
    except Exception as e:
        push_log(f"boot: audio FAILED: {e}", "sys")
        audio = None

    push_log("boot: initializing Camera", "sys")
    try:
        camera = CameraStreamer(device=0, width=640, height=480, fps=12, quality=65)
        if camera.start():
            push_log("boot: camera ready (640x480@20)", "sys")
        else:
            push_log(f"boot: camera FAILED: {camera.error}", "sys")
            camera = None
    except Exception as e:
        push_log(f"boot: camera FAILED: {e}", "sys")
        camera = None

    push_log("boot: initializing YOLO", "sys")
    if not _yolo_ok:
        push_log("boot: YOLO disabled (ultralytics not installed in this python)", "sys")
    else:
        try:
            yolo = YOLOInferencer(
                model_path=DEFAULT_YOLO_MODEL,
                get_jpeg_fn=(lambda: camera.get_jpeg()) if camera else (lambda: (None, 0)),
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

    # ---- ESP32 串口故障检测 + 自动重连(覆盖三种场景)----
    # 1) 开机时 car init 就失败 (USB 没插好 / ESP32 没上电) → 重试 init
    # 2) 运行中 USB 拔了 / 串口失效 → reopen
    # 3) ESP32 firmware 复位但 host serial 还有效 → 不动 (reader 会自己收数据)
    async def _serial_watchdog():
        nonlocal_dummy = None  # placeholder
        global car
        consec_fail = 0
        while True:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                return
            # 场景 1: car 整个没初始化成功 → 重试 CarController()
            if car is None:
                try:
                    push_log("serial watchdog: trying CarController init...", "sys")
                    new_car = CarController(
                        ultrasonic_callback=on_ultra,
                        ps3_l1_callback=on_ps3_l1,
                        ps3_l2_callback=on_ps3_l2,
                    )
                    car = new_car
                    install_event_hooks()
                    push_log("serial watchdog: car initialized after retry", "sys")
                    consec_fail = 0
                    try:
                        await asyncio.sleep(0.3)
                        car.ultrasonic_report_on()
                        push_log("serial watchdog: re-enabled ULTRA_REPORT", "sys")
                    except Exception:
                        pass
                except Exception as e:
                    consec_fail += 1
                    push_log(f"serial watchdog: car init failed #{consec_fail}: {e}", "sys")
                continue
            # 场景 2: car 存在但串口探测异常 → close + reopen
            try:
                if car.ser is None or not car.ser.is_open:
                    raise IOError("ser not open")
                _ = car.ser.in_waiting
                _ = car.ser.port
                consec_fail = 0
                continue
            except Exception as e:
                consec_fail += 1
                push_log(f"serial watchdog: fault #{consec_fail}: {e}", "sys")
            try: car.ser.close()
            except Exception: pass
            await asyncio.sleep(min(2 + consec_fail, 10))
            try:
                new_ser = _pyserial.Serial(
                    port=car.port, baudrate=car.baudrate, timeout=0.1,
                )
                car.ser = new_ser
                push_log(f"serial watchdog: reconnected ({car.port})", "sys")
                consec_fail = 0
            except Exception as e2:
                push_log(f"serial watchdog: reconnect failed: {e2}", "sys")
    _sw_task = asyncio.create_task(_serial_watchdog())

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

    push_log("system online", "sys")
    yield
    push_log("system shutdown", "sys")
    _mm_task.cancel()
    _sw_task.cancel()
    _rt_task.cancel()
    for obj, name in [(car, "car"), (audio, "audio"), (camera, "camera"), (yolo, "yolo"), (minimap, "minimap")]:
        if obj is None: continue
        try: obj.close() if hasattr(obj, "close") else obj.stop()
        except Exception: pass


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
    return HTMLResponse(INDEX_HTML)


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

    # camera
    cam_ok = camera is not None and camera.is_alive()
    cam_detail = f"/dev/video{camera.device} · {camera.width}x{camera.height}" if camera else "—"
    cam_fps = round(camera.fps(), 1) if camera else 0.0
    cam_age = None
    if camera and camera.last_frame_t:
        cam_age = int((time.time() - camera.last_frame_t) * 1000)

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
                       "- 避障: `obstacle_on` `obstacle_off` (与 ultra 互斥)\n"
                       "- 超声波上报: `ultra_on` `ultra_off`\n"
                       "- 急停: `emergency`\n"
                       "- 速度: `speed_up` `speed_down` `speed_reset`\n"
                       "- 音效: `cat1` `cat2` `cat3` `cat4`\n"))
async def cmd(action: str):
    if car is None:
        raise HTTPException(503, "car not ready")
    fn = CMD_MAP.get(action)
    if not fn:
        raise HTTPException(404, f"unknown action: {action}")
    try:
        fn()
    except Exception as e:
        push_log(f"cmd error: {action}: {e}", "sys")
        raise HTTPException(500, str(e))
    return {"ok": True, "action": action}


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


@app.post("/api/audio/play", tags=["audio"], summary="播放指定 wav",
          description="body: `{name: \"xxx.wav\", src: \"recordings\"|\"sounds\"}`")
async def audio_play(req: Request):
    if audio is None:
        raise HTTPException(503, "audio not ready")
    body = await req.json()
    name = body.get("name", "")
    src = body.get("src", "recordings")
    if "/" in name or ".." in name or not name:
        raise HTTPException(400, "bad name")
    base = REC_DIR if src == "recordings" else SOUND_DIR
    full = os.path.join(base, name)
    if not os.path.exists(full):
        raise HTTPException(404, "not found")
    try:
        audio.play_wav(full)
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
          description="body 任选字段:`{speaker: 0-100, mic: 0-100, agc: 0|1}`")
async def set_volume(req: Request):
    body = await req.json()
    out = {}
    for k_in, ctrl in (("speaker", "Speaker"), ("mic", "Mic"), ("agc", "Auto Gain Control")):
        if k_in in body:
            ok = _amixer_set(ctrl, body[k_in])
            out[k_in] = _amixer_get(ctrl) if ok else None
            push_log(f"vol {k_in} → {out[k_in]}%", "audio")
    return out


@app.post("/api/audio/stop", tags=["audio"], summary="停止当前播放")
async def audio_stop():
    if audio is None:
        raise HTTPException(503, "audio not ready")
    audio.stop_playback()
    push_log("audio playback stopped", "audio")
    return {"ok": True}


# ====================== Camera stream ======================

async def mjpeg_generator():
    boundary = b"--frame\r\n"
    last_t = 0.0
    while True:
        if camera is None:
            await asyncio.sleep(0.5)
            continue
        jpeg, t = camera.get_jpeg()
        if jpeg is None or t == last_t:
            await asyncio.sleep(0.03)
            continue
        last_t = t
        chunk = (boundary
                 + b"Content-Type: image/jpeg\r\n"
                 + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                 + jpeg + b"\r\n")
        yield chunk


@app.get("/api/camera/stream.mjpg", tags=["camera"], summary="MJPEG 摄像头流",
         description="multipart/x-mixed-replace 推送实时 JPEG 帧。浏览器 `<img src>` 直接显示;Python 用 `requests` 流式读取分段")
async def camera_stream():
    if camera is None:
        raise HTTPException(503, "camera not ready")
    return StreamingResponse(
        mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                 "Pragma": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/camera/snapshot.jpg", tags=["camera"], summary="当前帧 JPEG 快照(单张)")
async def camera_snapshot():
    if camera is None:
        raise HTTPException(503, "camera not ready")
    jpeg, _ = camera.get_jpeg()
    if not jpeg:
        raise HTTPException(503, "no frame yet")
    return StreamingResponse(iter([jpeg]), media_type="image/jpeg")


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
                if m.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong", "t": now_ms()}))
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        clients.discard(websocket)


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
  --bg:#0b0907; --bg2:#13100b; --panel:#1a1610; --panel2:#221c12;
  --line:#312715; --line2:#5a4923;
  --amber:#ffb000; --amber-d:#cf8a00; --amber-l:#ffd266;
  --red:#ff3a2e; --red-d:#a8221a;
  --green:#7fff5a; --cyan:#5af0ff;
  --text:#ece2c4; --text-dim:#897a59; --text-mute:#4f4530;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--text);
  font-family:var(--font-mono);user-select:none;-webkit-user-select:none;
}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:1;
  background:repeating-linear-gradient(0deg,rgba(255,176,0,.025) 0,rgba(255,176,0,.025) 1px,transparent 1px,transparent 3px),
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
  gap:1px;padding:8px;background:#1a1410;
}

.panel{background:var(--bg2);border:1px solid var(--line);position:relative;overflow:hidden;display:flex;flex-direction:column}
.panel-head{flex:0 0 28px;display:flex;align-items:center;padding:0 12px;
  background:linear-gradient(180deg,#1f1a12,#15110b);border-bottom:1px solid var(--line);
  font-family:var(--font-display);font-size:10.5px;letter-spacing:.22em;color:var(--amber);text-transform:uppercase;
}
.panel-head::before{content:"▸";margin-right:8px}
.panel-head .ph-r{margin-left:auto;color:var(--text-dim);font-size:9px;letter-spacing:.2em}
.map-reset-btn{margin-left:10px;background:transparent;border:1px solid var(--line2);
  color:var(--text-dim);font-family:var(--font-mono);font-size:9px;letter-spacing:.15em;
  padding:2px 8px;cursor:pointer;text-transform:uppercase;height:18px}
.map-reset-btn:hover{border-color:var(--amber);color:var(--amber);background:rgba(255,176,0,.06)}
.panel-body{padding:12px;flex:1;overflow:hidden;position:relative;display:flex;flex-direction:column;gap:10px}

header{grid-area:head;background:linear-gradient(90deg,#1a1410,#15110b 50%,#1a1410);
  border:1px solid var(--line);display:flex;align-items:center;padding:0 22px;position:relative;gap:14px;
}
header::after{content:"";position:absolute;bottom:-1px;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,var(--amber),transparent);opacity:.7;
}
.h-brand{font-family:var(--font-display);font-size:20px;letter-spacing:.32em;color:var(--amber);
  text-transform:uppercase;text-shadow:0 0 12px rgba(255,176,0,.4)}
.h-sub{font-size:10px;color:var(--text-dim);letter-spacing:.22em;text-transform:uppercase}
.h-right{margin-left:auto;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.h-stat{display:flex;align-items:center;gap:8px;font-size:10.5px;color:var(--text-dim);letter-spacing:.12em;text-transform:uppercase;cursor:default}
.h-stat[title]{cursor:help}
.dot{width:8px;height:8px;border-radius:50%;background:var(--text-mute);box-shadow:0 0 6px rgba(255,176,0,.4);transition:background .2s,box-shadow .2s}
.dot.ok{background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 2s infinite}
.dot.warn{background:var(--amber);box-shadow:0 0 8px var(--amber);animation:pulse 1.2s infinite}
.dot.error{background:var(--red);box-shadow:0 0 8px var(--red);animation:pulse .6s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
@keyframes flicker{0%,100%{opacity:1}50%{opacity:.55}}
@keyframes scan{0%{transform:translateY(-100%)}100%{transform:translateY(100%)}}

/* ============ CAM PANEL ============ */
.pane-cam{grid-area:cam}
.pane-cam .panel-body{gap:10px;padding:10px}

.cam-frame{position:relative;width:100%;aspect-ratio:4/3;background:#000;border:1px solid var(--line2);overflow:hidden;flex:0 0 auto}
.cam-frame::before{content:"";position:absolute;inset:0;
  background:repeating-linear-gradient(0deg,transparent 0,transparent 2px,rgba(255,176,0,.04) 2px,rgba(255,176,0,.04) 3px);
  pointer-events:none;z-index:2;
}
.cam-frame::after{content:"";position:absolute;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(255,176,0,.4),transparent);
  animation:scan 3.5s linear infinite;pointer-events:none;z-index:3;
}
.cam-hud{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:4}
.cam-hud rect.hud-bar.hot{animation:flicker .35s infinite}
/* REAR CAM 镜像:cam-img 水平翻转;bbox 由 JS 反转 x 坐标(保留文字方向);HUD/corner 不动 */
.cam-frame.mirror .cam-img{transform:scaleX(-1)}
.cam-img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;background:#000;display:block;z-index:1}
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
.filter-row input[type="text"]:focus{border-color:var(--amber);box-shadow:0 0 6px rgba(255,176,0,.25)}
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
    repeating-linear-gradient(0deg,rgba(255,176,0,.04) 0,rgba(255,176,0,.04) 1px,transparent 1px,transparent 24px),
    repeating-linear-gradient(90deg,rgba(255,176,0,.04) 0,rgba(255,176,0,.04) 1px,transparent 1px,transparent 24px),
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
.ureading-val{font-family:var(--font-tech);font-size:24px;color:var(--amber);text-shadow:0 0 8px rgba(255,176,0,.4);line-height:1}
.ureading-val.danger{color:var(--red);text-shadow:0 0 10px var(--red);animation:flicker .35s infinite}
.ureading-unit{font-size:10px;color:var(--text-mute);margin-left:5px}
.ureading-bar{margin-top:6px;height:3px;background:var(--bg);border:1px solid var(--line);position:relative}
.ureading-bar > div{height:100%;background:var(--amber);transition:width .2s}
.ureading.danger .ureading-bar > div{background:var(--red)}

/* ============ DECK ============ */
.pane-deck{grid-area:deck}
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
  background:linear-gradient(180deg,#5a4818,#ffb000);color:var(--bg);
  border-color:var(--amber);border-bottom-width:1px;transform:translateY(2px);
  box-shadow:0 0 22px rgba(255,176,0,.55),inset 0 -2px 4px rgba(0,0,0,.35);
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
.btn.on{background:rgba(255,176,0,.14);border-color:var(--amber);color:var(--amber);box-shadow:inset 0 0 14px rgba(255,176,0,.18)}
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
.log-line:hover{background:rgba(255,176,0,.05);border-left-color:var(--amber)}
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
.audio-item:hover{color:var(--amber);background:rgba(255,176,0,.05)}
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
<div id="app">
  <header>
    <div class="h-brand">ROBOT_CTRL</div>
    <div class="h-sub">// RPI-01 // 3WD</div>
    <div class="h-right">
      <div class="h-stat" title="WebSocket uplink"><div class="dot" id="dotLink"></div><span id="linkText">UPLINK</span></div>
      <div class="h-stat" title="ESP32 串口"><div class="dot" id="dotSerial"></div><span>SERIAL</span></div>
      <div class="h-stat" title="ESP32 心跳"><div class="dot" id="dotESP"></div><span id="espText">ESP32</span></div>
      <div class="h-stat" title="USB 音频"><div class="dot" id="dotAudio"></div><span>AUDIO</span></div>
      <div class="h-stat" title="USB 摄像头"><div class="dot" id="dotCam"></div><span>CAM</span></div>
      <div class="h-stat" title="树莓派 CPU 温度"><span style="color:var(--text-mute)">T°</span><span id="cpuTemp">--</span></div>
      <div class="h-stat" title="启动时长"><span style="color:var(--text-mute)">T+</span><span id="uptime">00:00:00</span></div>
    </div>
  </header>

  <section class="panel pane-cam">
    <div class="panel-head">CAM · LIVE FEED <span class="ph-r" id="camMeta">— FPS</span></div>
    <div class="panel-body">
      <div class="cam-frame">
        <img class="cam-img" id="camImg" alt="" referrerpolicy="no-referrer"/>
        <svg class="cam-hud" id="camHud" viewBox="0 0 400 300" preserveAspectRatio="none">
          <g id="hudMode" class="hud-badge">
            <rect x="6" y="6" width="132" height="20" fill="rgba(0,0,0,0.65)" stroke="#ffb000" stroke-width="1"/>
            <text x="72" y="20" text-anchor="middle" font-family="JetBrains Mono, ui-monospace, Menlo, monospace" font-size="11" fill="#ffb000" letter-spacing="2" id="hudModeText">STANDBY</text>
          </g>
          <g id="hudAlert" opacity="0">
            <rect x="262" y="6" width="132" height="20" fill="rgba(255,58,46,0.88)" stroke="#ff3a2e" stroke-width="1"/>
            <text x="328" y="20" text-anchor="middle" font-family="JetBrains Mono, ui-monospace, Menlo, monospace" font-size="11" fill="#0b0907" letter-spacing="2" font-weight="700">! OBSTACLE !</text>
          </g>
          <g id="yoloBoxes"></g>
        </svg>
        <div class="cam-corner tl"></div><div class="cam-corner tr"></div>
        <div class="cam-corner bl"></div><div class="cam-corner br"></div>
        <div class="cam-meta" id="camOSD">CAM_01 · 640x480</div>
        <div class="cam-rec"><div class="recdot"></div>LIVE</div>
        <div class="cam-overlay-err" id="camErr" style="display:none">NO_SIGNAL</div>
      </div>

      <div class="btn-row btn-2">
        <button class="btn" id="btnMirror"><span>⟷ REAR CAM</span><span class="badge" id="mirrorBadge">OFF</span></button>
        <div class="stat-card" style="text-align:left">
          <div class="stat-label">VIEW</div>
          <div class="stat-val" style="font-size:13px" id="viewLabel">VEHICLE</div>
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
              <stop offset="0%" stop-color="#ffb000" stop-opacity="0.55"/>
              <stop offset="100%" stop-color="#ffb000" stop-opacity="0"/>
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

            <rect x="-44" y="-36" width="88" height="72" fill="#1f1a10" stroke="#ffb000" stroke-width="1.5"/>
            <line x1="-44" y1="-20" x2="44" y2="-20" stroke="#5a4d2f" stroke-dasharray="2 2" opacity=".6"/>
            <line x1="-44" y1="0"   x2="44" y2="0"   stroke="#5a4d2f" stroke-dasharray="2 2" opacity=".6"/>
            <line x1="-44" y1="20"  x2="44" y2="20"  stroke="#5a4d2f" stroke-dasharray="2 2" opacity=".6"/>

            <polygon points="-10,-28 10,-28 0,-40" fill="#ffb000"/>
            <circle cx="0" cy="0" r="2.5" fill="#ffb000"/>
            <text x="0" y="12" text-anchor="middle" font-size="9" fill="#5a4d2f" font-family="JetBrains Mono, ui-monospace, Menlo, monospace">RPI-01</text>

            <g><rect x="-6" y="-40" width="12" height="5" fill="#1a1610" stroke="#ffb000"/>
               <text x="0" y="-46" text-anchor="middle" font-size="8" fill="#8a7d5e">S1</text></g>
            <g transform="rotate(-50)"><rect x="-6" y="-40" width="12" height="5" fill="#1a1610" stroke="#ffb000"/>
               <text x="0" y="-46" text-anchor="middle" font-size="8" fill="#8a7d5e">S2</text></g>
            <g transform="rotate(50)"><rect x="-6" y="-40" width="12" height="5" fill="#1a1610" stroke="#ffb000"/>
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
    <div class="panel-head">INPUT DECK · MANUAL OVERRIDE <span class="ph-r" id="lastActChip">STOP</span></div>
    <div class="panel-body">
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

      <div style="flex:1;display:flex;flex-direction:column;min-height:0">
        <div class="sec-label">RECORDINGS · CLICK TO PLAY</div>
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

  // ============ Camera image ============
  // Use single <img> with mjpeg endpoint. Add cache-buster on (re)mount.
  function startCam(){
    const img = $('#camImg');
    img.onerror = () => {
      $('#camErr').style.display = 'flex';
      setTimeout(() => { img.src = `/api/camera/stream.mjpg?t=${Date.now()}`; }, 1500);
    };
    img.onload = () => { $('#camErr').style.display = 'none'; };
    img.src = `/api/camera/stream.mjpg?t=${Date.now()}`;
  }
  startCam();

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
        renderYoloBoxes(m.data.detections);
        $('#yoloStat').textContent = `${m.data.inference_ms}ms · ${m.data.detections.length}`;
      }
      else if (m.type === 'map'){ applyMap(m.data); }
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
    const frame = document.querySelector('.cam-frame');
    if (frame) frame.classList.toggle('mirror', rearMode);
    const btn = $('#btnMirror');
    if (btn) btn.classList.toggle('on', rearMode);
    const badge = $('#mirrorBadge');
    if (badge) badge.textContent = rearMode ? 'ON' : 'OFF';
    const lbl = $('#viewLabel');
    if (lbl) lbl.textContent = rearMode ? 'CAMERA' : 'VEHICLE';
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
    // A/D 配合水平镜像后已经对了
    'a':'left','arrowleft':'left',
    'd':'right','arrowright':'right',
    // 反转 Q↔E 让旋转方向跟画面世界滚动方向一致
    'q':'rotate_cw','e':'rotate_ccw',
  };
  function moveMap(){ return rearMode ? moveMapRear : moveMapNormal; }
  const HOLD_MS = 100;
  const holdTimers = {};
  function startHold(action){
    if (holdTimers[action]) return;
    cmd(action);
    holdTimers[action] = setInterval(() => cmd(action), HOLD_MS);
  }
  function stopHold(action){
    if (!holdTimers[action]) return;
    clearInterval(holdTimers[action]); delete holdTimers[action];
    if (Object.keys(holdTimers).length === 0) cmd('stop');
  }

  // ============ Keyboard ============
  const pressed = new Map();  // key -> action(根据 rearMode 决定的实际 cmd)
  let recording = false;
  document.addEventListener('keydown', ev => {
    if (ev.repeat) return;
    if (ev.target && (ev.target.tagName === 'INPUT' || ev.target.tagName === 'TEXTAREA')) return;
    const k = ev.key.toLowerCase();
    const action = moveMap()[k];
    if (action){
      if (pressed.has(k)) return;
      pressed.set(k, action);
      startHold(action); highlightKey(k, true);
      ev.preventDefault(); return;
    }
    switch(k){
      case ' ': doEmergency(); ev.preventDefault(); break;
      case 'escape': cmd('stop'); audioApi('/api/audio/stop'); break;
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
    left: 'left', right: 'right',
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

  // ============ Toggles (handle ESP32 mutual exclusion) ============
  let modeObs = false, modeUltra = false;
  async function setObs(on){
    if (on && modeUltra){
      // ESP32 互斥:开避障必须先关超声波上报
      cmd('ultra_off');
      await new Promise(r => setTimeout(r, 150));
    }
    cmd(on ? 'obstacle_on' : 'obstacle_off');
  }
  async function setUltra(on){
    if (on && modeObs){
      cmd('obstacle_off');
      await new Promise(r => setTimeout(r, 150));
    }
    cmd(on ? 'ultra_on' : 'ultra_off');
  }
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
      rect.setAttribute('stroke', em ? '#ff3a2e' : (modeObs || modeUltra ? '#7fff5a' : '#ffb000'));
    }
    if (t) t.setAttribute('fill', em ? '#ff3a2e' : (modeObs || modeUltra ? '#7fff5a' : '#ffb000'));
  }

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
        const strokeColor = danger ? '#ff3a2e' : '#ffb000';
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
  const yoloPalette = ['#7fff5a','#ffb000','#5af0ff','#ff5af0','#ff8a2e','#a5e8ff','#ffd266','#7affb0','#ff9a9a','#c19aff'];
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
    if (!yoloEnabled){ renderYoloBoxes([]); $('#yoloStat').textContent = '— ms · 0'; }
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
