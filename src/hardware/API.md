# Robot Control Unit — HTTP / WebSocket API

> 树莓派上的 yahboom 3WD 全向轮机器人小车 web 控制台,所有数据与控制能力以 HTTP / WebSocket 暴露给本机其他程序。

- 监听:`0.0.0.0:8000`
- 访问:`http://192.168.0.170:8000` 或 `http://localhost:8000`
- 交互式 API 文档(Swagger UI):`/docs`
- 备选 ReDoc:`/redoc`
- OpenAPI JSON:`/openapi.json`

---

## 端点总览

| 分类 | 方法 | 路径 | 说明 |
|---|---|---|---|
| UI | GET | `/` | 控制台 HTML 页面 |
| state | GET | `/api/state` | 完整状态 + 最近日志 |
| state | GET | `/api/health` | 模块连通性 + 系统监控(CPU/风扇/内存) |
| control | POST | `/api/cmd/{action}` | 发送控制指令 |
| audio | POST | `/api/audio/rec/start` | 开始录音 |
| audio | POST | `/api/audio/rec/stop` | 停止并保存录音 |
| audio | GET | `/api/recordings` | 列出录音文件 |
| audio | POST | `/api/audio/play` | 播放 wav |
| audio | POST | `/api/audio/stop` | 停止播放 |
| camera | GET | `/api/camera/stream.mjpg` | MJPEG 实时流 |
| camera | GET | `/api/camera/snapshot.jpg` | 单帧 JPEG 快照 |
| map | GET | `/api/map/state` | MiniMap pose + occupancy grid 快照 |
| map | POST | `/api/map/reset` | 清空地图 + pose 归零 |
| map | POST | `/api/map/config` | 标定速度模型常数 (k_linear / k_angular) |
| ws | WS | `/ws` | 实时事件推送 |

---

## 1. 状态查询

### `GET /api/state`

返回当前完整状态对象,以及最近 80 条日志。

**响应**
```json
{
  "state": {
    "ultra": {
      "1": {"distance_mm": 234, "has_obstacle": false, "t": 1778900000000},
      "2": {"distance_mm": 234, "has_obstacle": false, "t": 1778900000000},
      "3": {"distance_mm":  60, "has_obstacle": true,  "t": 1778900000000}
    },
    "modes": {"obstacle": false, "ultra_report": true, "emergency": false},
    "speed": 2000,
    "last_action": "STOP",
    "recording": false,
    "rec_path": null
  },
  "logs": [
    {"t": 1778900000123, "kind": "esp32", "line": "OBSTACLE_ON"},
    {"t": 1778900000456, "kind": "cmd",   "line": "tx: FORWARD"},
    {"t": 1778900000789, "kind": "audio", "line": "rec start: web_..."}
  ]
}
```

| 字段 | 说明 |
|---|---|
| `ultra[1..3]` | 前 / 左前 / 右前 三个超声波,`distance_mm` 单位毫米,`has_obstacle` ESP32 判定有障碍 |
| `modes.obstacle` | ESP32 自主避障 ON/OFF |
| `modes.ultra_report` | 上层 App 自定义避障 — 超声波上报模式 ON/OFF(与 obstacle 互斥) |
| `modes.emergency` | 急停态。任何动作会清掉 |
| `speed` | ESP32 当前 PWM(从 `BUTTON_SPEED,XXXX` 解析) |
| `last_action` | 最后发送的动作字面值 `FORWARD/STOP/...` |
| `recording` / `rec_path` | 当前录音状态 |
| `logs[].kind` | `esp32 / cmd / audio / ps3 / sys` |

### `GET /api/health`

模块连通性 + 系统监控。客户端推荐 2 秒 poll 一次。

**响应**
```json
{
  "modules": {
    "serial":  {"ok": true, "detail": "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"},
    "esp32":   {"ok": true, "detail": "last 123ms ago"},
    "audio":   {"ok": true, "detail": "plughw:CARD=Device,DEV=0"},
    "camera":  {"ok": true, "detail": "/dev/video0 · 640x480", "fps": 12.1, "frame_age_ms": 70},
    "fan":     {"ok": true, "detail": "3294 rpm · 68.6%"},
    "yolo":    {"ok": true, "detail": "197 ms · 3 det"}
  },
  "system": {
    "cpu_temp_c": 55.4,
    "load_1m": 1.72,
    "mem_pct": 23.6,
    "uptime_s": 245,
    "clients": 1,
    "fan_rpm": 3294,
    "fan_pwm": 175,
    "fan_duty_pct": 68.6,
    "fan_cooling_state": "3/4"
  }
}
```

**`modules[].ok` 判定**(每个模块都不一样,客户端别一刀切):

| 模块 | ok=true 含义 | ok=false 常见原因 |
|---|---|---|
| `serial` | `car.ser.is_open` — 串口句柄打开 | USB 拔了 / CarController 初始化失败 |
| `esp32` | 最近 **3 秒** 内 ESP32 发过任何串口行(ULTRA/状态/启动消息任意) | ESP32 idle 不主动推数据时也会 false,**不一定代表 ESP32 挂了** — 发个 `speed_reset` 之类的会触发 `BUTTON_SPEED` 回应,马上变 true |
| `audio` | `AudioController` 实例化成功(USB 音频设备可枚举) | 音频卡未插 / aplay 不可用 |
| `camera` | `VideoCapture.isOpened()` 且最近 **3 秒** 有帧 | 摄像头被占用 / 拔了 |
| `fan` | hwmon 找到 `pwmfan` 设备且 RPM > 0 | dtoverlay 没启用 / 风扇没接 4-pin |
| `yolo` | `enabled=true` 且 `latest_inference_t > 0`(至少推理过一次) | 默认 `disabled`,要 POST `/api/yolo/config {enabled:true}` 启用 |

`detail` 字段是给人看的诊断字符串,**不要 parse**,程序逻辑只依赖 `ok` + 数值字段(fps / frame_age_ms / fan_rpm 等)。

---

## 2. 车辆控制

### `POST /api/cmd/{action}`

所有 action 是单字符串,**幂等**(再次发送相同动作不会出错)。

| action | 行为 |
|---|---|
| `forward` / `backward` / `left` / `right` | 4 方向移动(全向轮平移) |
| `rotate_cw` / `rotate_ccw` | 顺时针 / 逆时针自转 |
| `stop` | 停止移动 |
| `obstacle_on` / `obstacle_off` | ESP32 自主避障(与 `ultra_*` 互斥) |
| `ultra_on` / `ultra_off` | 超声波上报模式(ESP32 不主动避障,持续推 ULTRA 数据) |
| `emergency` | 急停。关掉所有模式 |
| `speed_up` / `speed_down` / `speed_reset` | ESP32 BUTTONSPEED ±200 / 重置 |
| `cat1` / `cat2` / `cat3` / `cat4` | 播放猫叫(本机 aplay) |

**持续按住前进**:前端按 100ms 间隔重发指令,松开发 `stop`。后端不维持运动状态,所以单次 `forward` 后小车跑很短一段就停。客户端需自己实现"hold-to-move":

```python
import requests, time, threading
def hold(action, duration_s):
    end = time.time() + duration_s
    while time.time() < end:
        requests.post(f"http://192.168.0.170:8000/api/cmd/{action}")
        time.sleep(0.1)
    requests.post("http://192.168.0.170:8000/api/cmd/stop")
hold("forward", 2.0)  # 前进 2 秒
```

**互斥处理**:open `obstacle_on` 时 ESP32 可能返回 `AUTO_OBSTACLE_BLOCKED_ULTRA_REPORT_ON`(被 ultra 占用),此时 state 自动同步为 obstacle=false。客户端应先 `ultra_off` 再 `obstacle_on`。

**响应**:`{"ok": true, "action": "forward"}` 或 `400/404/503` HTTP 错误。

---

## 3. 音频

### `POST /api/audio/rec/start`

立即开始录音到 `/home/pi/car_project/records/web_YYYYMMDD_HHMMSS.wav` (16kHz mono S16_LE)。返回保存路径。如已经在录,返回 `{"ok": false, "reason": "already recording"}`。

### `POST /api/audio/rec/stop`

停止录音并写入 wav 文件头。返回 `{"ok": true, "path": "..."}`。

### `GET /api/recordings`

按 mtime 降序列出最近 30 条 wav。
```json
{"items": [
  {"name": "web_20260516_113035.wav", "size": 17141, "mtime": 1778900000}
]}
```

### `POST /api/audio/play`

播放 wav。body:
```json
{"name": "cat1.wav", "src": "sounds"}
```
- `src`: `"recordings"` (默认) 或 `"sounds"`
- `name`: 不允许 `/` 或 `..`

### `POST /api/audio/stop`

停止当前播放。

---

## 4. 摄像头

### `GET /api/camera/stream.mjpg`

MJPEG (`multipart/x-mixed-replace; boundary=frame`)。

**浏览器**:`<img src="/api/camera/stream.mjpg">` 直接显示。

**Python**:
```python
import requests
r = requests.get("http://192.168.0.170:8000/api/camera/stream.mjpg", stream=True)
boundary = b"--frame"
buf = b""
for chunk in r.iter_content(8192):
    buf += chunk
    # 用 boundary 分割,提取 JPEG segments
```

### `GET /api/camera/snapshot.jpg`

最新一帧 JPEG 单图。

```bash
curl -o snap.jpg http://192.168.0.170:8000/api/camera/snapshot.jpg
```

---

## 5. WebSocket — 实时推送

`WS /ws`

**连接成功**后立即收到一条 `snapshot`,之后按事件推送增量。客户端可发 `{"type":"ping"}` 维持连接。

### 消息类型

```json
{"type": "snapshot",
 "data": {"state": {...full state...}, "logs": [...recent...]}}
```

```json
{"type": "ultra",
 "data": {"1": {"distance_mm":234,"has_obstacle":false,"t":1778...},
          "2": {...}, "3": {...}}}
```
ESP32 每 ~50ms 推一次,频率较高。

```json
{"type": "state", "data": {/* full state object */}}
```
模式变化 / 速度变化 / 录音状态变化时触发。

```json
{"type": "log",
 "data": {"t": 1778900123456, "kind": "esp32", "line": "OBSTACLE_ON"}}
```
`kind`:`esp32 / cmd / audio / ps3 / sys`。`ULTRA,...` 行不进日志,走专门的 `ultra` channel。

### Python WS 客户端示例

```python
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://192.168.0.170:8000/ws") as ws:
        async for raw in ws:
            m = json.loads(raw)
            if m["type"] == "ultra":
                s3 = m["data"]["3"]
                if s3["has_obstacle"]:
                    print("右前障碍:", s3["distance_mm"], "mm")
            elif m["type"] == "log":
                print(m["data"]["kind"], "|", m["data"]["line"])

asyncio.run(main())
```

---

## 6. 数据模型速查

```ts
type State = {
  ultra: { 1: Sonar|null, 2: Sonar|null, 3: Sonar|null };
  modes: { obstacle: bool, ultra_report: bool, emergency: bool };
  speed: int | null;          // ESP32 PWM
  last_action: string;        // FORWARD / STOP / OBSTACLE_ON ...
  recording: bool;
  rec_path: string | null;
};
type Sonar = { distance_mm: int, has_obstacle: bool, t: int /* ms epoch */ };
type LogEntry = { t: int /* ms epoch */, kind: "esp32"|"cmd"|"audio"|"ps3"|"sys", line: string };
type Health = {
  modules: {
    serial: {ok, detail}, esp32: {ok, detail}, audio: {ok, detail},
    camera: {ok, detail, fps, frame_age_ms},
    fan:    {ok, detail}
  };
  system: {
    cpu_temp_c, load_1m, mem_pct, uptime_s, clients,
    fan_rpm, fan_pwm, fan_duty_pct, fan_cooling_state
  };
};
```

---

## 7. MiniMap (dead-reckoning + 占用栅格)

**纯软件**,**仅内存**,**服务重启清空**(用户明确要求,避免标定漂移污染下次运行)。

### 工作原理

- **位置估计**:监听 `wrapped_send` 发出的 `cmd action`,在后台线程每 50ms 步进:
  - `FORWARD/BACKWARD/LEFT/RIGHT` → 局部坐标系平移,通过当前 `theta` 旋转到世界坐标
  - `ROT_CW / ROT_CCW` → 旋转 `theta`
  - `STOP / EMERGENCY_STOP / 其他` → 清零 `current_action`,不再积分
- **速度模型**:`v = K_LINEAR × BUTTONSPEED` (mm/s), `ω = K_ANGULAR × BUTTONSPEED` (rad/s)。`BUTTONSPEED` 从 ESP32 上报的 `BUTTON_SPEED,XXXX` 行读取
- **障碍点投影**:每次收到 `ULTRA,sensor_id,distance,has_obstacle` 且 `has_obstacle=1`,根据当前 `pose` + sensor 在车体的角度 (S1=0°, S2=+50°, S3=-50°) + 50mm 前移,反投影成世界坐标,落到 10cm 占用 cell 累加 hit count
- **轨迹采样**:每 ~500ms 采当前 pose 一次,最多保留 300 点

### 渲染坐标

前端 ego-centric 视图:车始终在 SVG (200, 200) 朝上,世界相对车反向滚动 + 反向旋转。Scale 0.04 px/mm,viewBox 400px 覆盖 10m。

### `GET /api/map/state`

```json
{
  "pose": {"x": 234.5, "y": -120.3, "theta": 0.785, "theta_deg": 45.0},
  "current_action": "FORWARD",
  "button_speed": 2000,
  "cell_mm": 100,
  "cells": [[300, 0, 4], [400, 0, 2], ...],   // [wx_mm, wy_mm, hit_count]
  "cell_count": 17,
  "recent_hits": [{"t": 1778900000, "x": 300, "y": 50, "sensor": 1}, ...],
  "pose_history": [{"x": 0, "y": 0, "t": ...}, ...],
  "config": {"k_linear": 0.05, "k_angular": 0.0006}
}
```

### `POST /api/map/reset`

清空 grid + history,pose 归零 `(0, 0, 0)`。

### `POST /api/map/config`

body: `{k_linear: 0.05, k_angular: 0.0006}` (任一可选)。**这是标定接口** — 默认常数是猜的,跑一段已知距离 / 转动 90° 校准:

```bash
# 跑直线 1 米,实测车走了 1.5 米 → K_LINEAR 应该减小 1/1.5 倍
curl -X POST -d '{"k_linear": 0.0333}' http://pi:8000/api/map/config

# 转 360° cmd,实测只转了 270° → K_ANGULAR 增大 360/270
curl -X POST -d '{"k_angular": 0.0008}' http://pi:8000/api/map/config
```

### WebSocket

`{"type":"map","data": <snapshot 同 GET /api/map/state>}` 每 200ms 推送一次(仅当有 WS 客户端时)。

### 注意

dead-reckoning **没有任何修正信号**(无 IMU / 无视觉里程计 / 无轮编码反馈),误差会**单调累积**。短时(< 1 分钟)局部地图勉强可用,长时间运行 + 大量旋转会让地图完全错位。这是个**演示性可视化**,不是真 SLAM。要做真的,得加 IMU(yaw 漂移率从 °/s 降到 °/min)或视觉里程计。

---

## 8. 服务管理

```bash
sudo systemctl status   robot-console     # 状态
sudo systemctl restart  robot-console     # 重启
sudo systemctl stop     robot-console     # 停止
sudo systemctl disable  robot-console     # 关掉开机自启
journalctl -u robot-console -f            # 实时日志

sudo systemctl status   fan-curve         # 风扇激进曲线(开机自动应用 trip points)
```

实现:`/home/pi/Desktop/web_console.py`(单文件 ~700 行),无需 venv,系统 Python 3.11。

依赖:`fastapi`, `uvicorn[standard]`, `pyserial`, `opencv-python-headless`(可选)。

---

## 9. 典型客户端片段

### bash + curl
```bash
# 前进 2 秒
for i in {1..20}; do curl -s -X POST http://localhost:8000/api/cmd/forward >/dev/null; sleep 0.1; done
curl -s -X POST http://localhost:8000/api/cmd/stop

# 拉一次状态
curl -s http://localhost:8000/api/state | jq

# 播放 cat1
curl -s -X POST http://localhost:8000/api/cmd/cat1
```

### Python 同步
```python
import requests
S = "http://localhost:8000"

# 急停
requests.post(f"{S}/api/cmd/emergency")

# 看模块健康
h = requests.get(f"{S}/api/health").json()
print("CPU", h["system"]["cpu_temp_c"], "°C  fan", h["system"]["fan_rpm"], "rpm")

# 录 5 秒
requests.post(f"{S}/api/audio/rec/start")
import time; time.sleep(5)
r = requests.post(f"{S}/api/audio/rec/stop").json()
print("saved:", r["path"])
```

### Python 异步 + WS
见上文 §5。
