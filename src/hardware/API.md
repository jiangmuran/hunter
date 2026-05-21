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
| camera | GET | `/api/camera/list` | 所有 cam + 状态(多 cam 时枚举 a/b/c...) |
| camera | GET | `/api/camera/stream.mjpg` | 主 cam MJPEG(兼容,等价 `/{main_id}/stream.mjpg`) |
| camera | GET | `/api/camera/snapshot.jpg` | 主 cam 单帧 JPEG |
| camera | GET | `/api/camera/{cam_id}/stream.mjpg` | 指定 cam MJPEG(cam_id ∈ a/b/...) |
| camera | GET | `/api/camera/{cam_id}/snapshot.jpg` | 指定 cam 单帧 JPEG |
| camera | POST | `/api/camera/config` | 切换主 cam (`{main_id}`) |
| arm | GET | `/api/arm/status` | 机械臂状态(joint 角度 / 笛卡尔位置) |
| arm | POST | `/api/arm/home` | 所有 joint 归零 |
| arm | POST | `/api/arm/move_joint` | 单关节绝对角度 (`{joint, target_deg}`) |
| arm | POST | `/api/arm/nudge_joint` | 单关节增量 (`{joint, delta_deg}`) |
| arm | POST | `/api/arm/move_cartesian` | 末端绝对位置 (`{x,y,z}` 米) |
| arm | POST | `/api/arm/nudge_cartesian` | 末端笛卡尔增量 |
| arm | POST | `/api/arm/gripper` | 夹爪开/合增量 (`{delta_deg}`) |
| arm | POST | `/api/arm/torque` | 开/关 servo 锁紧 (`{on}`) |
| map | GET | `/api/map/state` | MiniMap pose + occupancy grid 快照 |
| map | POST | `/api/map/reset` | 清空地图 + pose 归零 |
| map | POST | `/api/map/config` | 标定模型常数 (k_linear / k_angular / backward_ratio) |
| rec | GET | `/api/rec/status` | 录制 / 回放状态 |
| rec | POST | `/api/rec/start` | 开始录制(清空已有事件) |
| rec | POST | `/api/rec/stop` | 停止录制 |
| rec | POST | `/api/rec/clear` | 清空录制(idle 时) |
| rec | POST | `/api/rec/play` | 开始回放 (`?direction=forward/reverse&speed&calibrate`) |
| rec | POST | `/api/rec/stop_playback` | 终止回放 |
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

### `POST /api/cmd/{action}` — 单条指令(瞬时)

所有 action 是单字符串,**幂等**:

| action | 行为 |
|---|---|
| `forward` / `backward` / `left` / `right` | 4 方向移动(全向轮平移) |
| `rotate_cw` / `rotate_ccw` | 顺时针 / 逆时针自转 |
| `stop` | 停止移动 |
| `obstacle_on` / `obstacle_off` | **软件避障**(后端拦截 → 启用 ULTRA_REPORT + 服务端检距停车,跟 ultra 兼容,不再互斥) |
| `ultra_on` / `ultra_off` | 超声波上报模式开关 |
| `emergency` | 急停。清掉所有模式 |
| `speed_up` / `speed_down` / `speed_reset` | ESP32 BUTTONSPEED ±200 / 重置 |
| `cat1` / `cat2` / `cat3` / `cat4` | 播放猫叫(本机 aplay) |

**响应**:`{"ok": true, "action": "forward", "mode": "single"}`

### `POST /api/cmd/{action}?{duration|distance|angle}=N` — 持续模式 ⭐ 推荐脚本用

三种模式互斥(优先级 `angle` > `distance` > `duration`):

| 参数 | 单位 | 适用 action | 含义 |
|---|---|---|---|
| `duration` | 秒(0.05-60) | 所有方向 | 直接指定持续时间 |
| `distance` | mm | `forward` `backward` `left` `right` | 行驶距离,基于 `K_LINEAR × BUTTON_SPEED` 估算时间 |
| `angle` | 度 | `rotate_cw` `rotate_ccw` | 转动角度,基于 `K_ANGULAR × BUTTON_SPEED` 估算时间 |

后端 `HoldController` 用**绝对时间步进** 100ms 给 ESP32 发该 cmd(不是 `time.sleep(0.1)`,所以 cycle 间隔不受 `car.method()` 耗时波动影响),时间到自动 stop。**HTTP 阻塞直到完成才返回。**

```bash
# 直走 500mm (粗略;实际依赖硬件 + K_LINEAR 标定)
curl -X POST 'http://pi:8000/api/cmd/forward?distance=500'

# 顺时针转 90 度
curl -X POST 'http://pi:8000/api/cmd/rotate_cw?angle=90'

# 直接指定时间(不基于 K 估算)
curl -X POST 'http://pi:8000/api/cmd/forward?duration=2'

# 链式动作
curl -X POST 'http://pi:8000/api/cmd/forward?distance=300' && \
curl -X POST 'http://pi:8000/api/cmd/rotate_cw?angle=90' && \
curl -X POST 'http://pi:8000/api/cmd/forward?distance=300'
```

**特点**:
- 软件时序 jitter 极低(绝对时间步进 + WebSocket 边沿信号触发,网络延迟不影响中间过程)
- 不用客户端循环 / stop cleanup
- 跟 web UI WS hold 共享同一个 `HoldController`(后到者 override)
- 60 秒上限防脚本失控

### ⚠️ 精度限制 — 别期望毫米级 / 度级精确

**走不直**:四电机硬件差异 + ESP32 内部 PWM 占空比不完全对称,**没有 IMU/编码器反馈纯软件无法纠偏**。每次跑 1 米都可能偏 5-10cm。

**转角度不固定**:`K_ANGULAR` 默认值是猜的,**必须标定**才靠谱:
```bash
# 跑测试:让车 rotate_cw 5 秒,目测转了多少度,反向算 K_ANGULAR
# K_ANGULAR = (实际转角 / 5秒) / BUTTON_SPEED 然后转弧度
# 例:5 秒转了 300° → 60°/s = 1.047 rad/s → K_ANGULAR = 1.047 / 2000 ≈ 0.0005
curl -X POST 'http://pi:8000/api/map/config' \
     -H 'Content-Type: application/json' \
     -d '{"k_angular": 0.0005, "k_linear": 0.045}'
```
标定持久化到 minimap 实例(服务重启重置,目前没存盘)。

要更高精度只能加硬件:**MPU6050 / BMI160 IMU** 接 pi I2C 拿 yaw,做闭环 PID 纠偏。

### 实时高频按键(Web UI 走 WS,不需要客户端关心)

Web UI 内部走 WebSocket `{type:'hold', state:'down|renew|up', action}` 通知后端 HoldController。脚本 / 第三方客户端**不建议**走这条,直接用上面 `duration=N` 简单可靠。

### Python 一行调用示例

```python
import requests

S = "http://192.168.0.170:8000"

def move(action, duration=None):
    """阻塞直到动作完成。"""
    params = {"duration": duration} if duration else {}
    requests.post(f"{S}/api/cmd/{action}", params=params)

move("forward", 2)            # 前进 2 秒
move("rotate_cw", 1)          # 转 1 秒
move("backward", 1.5)         # 后退 1.5 秒
move("stop")                  # 单次 stop
move("emergency")             # 急停
move("cat1")                  # 播放猫叫 1
```

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

## 10. 6 关节机械臂 (Sts3215 servo bus, 可选)

后端基于 [`physical_agent`](https://github.com/zhanghy12/Minimal_interface_moce) 包装 LeRobot `FeetechMotorsBus`。**机械臂代码不在 pi 上时,arm 自动 disabled,所有 `/api/arm/*` 返 503;装好后自动接管,无需重启服务**。

### 接线 / 安装

```bash
# 1. clone 到 pi
git clone https://github.com/zhanghy12/Minimal_interface_moce /home/pi/Minimal_interface_moce
cd /home/pi/Minimal_interface_moce
/home/pi/yolo_env/bin/pip install -e .

# 2. 写配置
cp configs/arm7_sts3215.example.json ~/Desktop/arm_config.json
# 编辑 ~/Desktop/arm_config.json:
#   "port": "/dev/serial/by-id/usb-1a86_USB_Serial-XXXX"  ← Feetech 总线 USB serial (不是 ESP32 那条)
#   每个 joint 的 zero_position_raw / motor_id 按你的实际舵机校准

# 3. 重启 service
sudo systemctl restart robot-console
```

`/api/health` 会显示 `arm: {"ok": true, "detail": "7 joints online"}` 表示就绪。

### Endpoints

| 路径 | 用途 | body |
|---|---|---|
| `GET /api/arm/status` | 状态(关节角度 / 末端 xyz / connected) | — |
| `POST /api/arm/home` | 所有关节回零 | `{speed_deg_s?}` |
| `POST /api/arm/nudge_cartesian` | 末端笛卡尔**增量**(米) | `{dx, dy, dz, speed_deg_s?}` |
| `POST /api/arm/move_cartesian` | 末端笛卡尔**绝对**位置(米) | `{x, y, z, speed_deg_s?}` |
| `POST /api/arm/nudge_joint` | 单关节增量(度) | `{joint: "joint_1", delta_deg, speed_deg_s?}` |
| `POST /api/arm/gripper` | 夹爪增量(度;正=开,负=合) | `{delta_deg, speed_deg_s?}` |

### Python 客户端示例

```python
import requests
S = "http://192.168.0.170:8000"

# 末端朝前走 10mm
requests.post(f"{S}/api/arm/nudge_cartesian", json={"dx": 0.01})

# 夹爪打开 10°
requests.post(f"{S}/api/arm/gripper", json={"delta_deg": 10})

# 单关节 joint_1 转 5°
requests.post(f"{S}/api/arm/nudge_joint", json={"joint": "joint_1", "delta_deg": 5})

# 回零
requests.post(f"{S}/api/arm/home")

# 看当前 xyz
st = requests.get(f"{S}/api/arm/status").json()
if st.get("cartesian_position_m"):
    print("xyz mm:", {k: v*1000 for k, v in st["cartesian_position_m"].items()})
```

### Web UI

DECK 面板顶部 tab `DRIVE | ARM`,切到 ARM:
- 顶部 X/Y/Z 当前位置(mm)
- 6 个方向按钮 `±X` `±Y` `±Z`,每次 10mm
- GRIPPER `◐ OPEN` / `◑ CLOSE` 各 ±5°
- `⌂ HOME` 一键回零
- `► JOINTS · ±2°` 折叠区 — 单关节 `−` `+` 精调

紧凑设计:复用 `.stat-card` `.key` `.btn` 现有样式,跟驱动控制 tab 共享 DECK 空间,只在切换时显示 ARM 内容。

---

## 11. 典型客户端片段

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

---

## 11. 多摄像头 (Multi-Camera)

启动时 probe `/dev/video0..9`,把可 open + read 一帧成功的设备按枚举顺序赋 id `a/b/c/d`(上限 4)。默认主 cam = 第一个 alive 的。

> ⚠️ 多 cam 同一 USB hub / controller 会因 isochronous 带宽 allocation **死锁**(即使总带宽 480Mbps 够),症状是 cam read() 永久 hang。解法:物理上分到 RPi 不同 USB 控制器(RPi 5 有 Bus 01 + Bus 03 两个独立 USB 2.0 controller)。

### `GET /api/camera/list`
返回所有 cam 状态。
```json
{
  "cameras": [
    {"id": "a", "is_main": true, "ok": true, "device": "/dev/video0", "fps": 12.0, "frame_age_ms": 47, "error": null},
    {"id": "b", "is_main": false, "ok": true, "device": "/dev/video2", "fps": 12.0, "frame_age_ms": 34, "error": null}
  ],
  "main_id": "a"
}
```

### `GET /api/camera/{cam_id}/stream.mjpg` 和 `/snapshot.jpg`
取指定 cam 流。`cam_id` 是 `a/b/...`,跟 `/api/camera/list` 返回的一致。

### `POST /api/camera/config`
切换主 cam。
```json
POST /api/camera/config
{"main_id": "b"}
→ {"ok": true, "main_id": "b"}
```
切主后 `/api/camera/stream.mjpg`(兼容路径)指向新主;YOLO 推理 source 跟着切;`/api/health.modules.camera.detail` 报新 main。

---

## 12. 机械臂 (Arm — Sts3215 × 6 joints)

依赖 `physical-agent` + `lerobot[feetech]`,通过 USB-serial(CDC ACM 或 CH340)连 Feetech 总线,**外部电源**给 servo 供电。Config 在 `~/Desktop/arm_config.json`。Joint 顺序:`joint_1..6`。

### `GET /api/arm/status`
关节角度 / 笛卡尔末端 / 启动 raw 位置等。

### `POST /api/arm/torque`
开/关 servo 锁紧。
```json
{"on": false}  // OFF 可徒手扳动
{"on": true}   // ON  锁紧持位,响应 move 命令
→ {"ok": true, "torque_on": true}
```

### `POST /api/arm/move_joint`
单关节绝对角度。
```json
{"joint": "joint_2", "target_deg": 45.0, "speed_deg_s": 20}
→ {"ok": true, "joint": "joint_2", "goal_raw": 2560}
```

### `POST /api/arm/nudge_joint`
单关节相对增量。
```json
{"joint": "joint_3", "delta_deg": -2}
```

### `POST /api/arm/move_cartesian` / `nudge_cartesian`
末端 xyz 米为单位(需要 urdf):
```json
{"x": 0.18, "y": 0.0, "z": 0.20}
```

### `POST /api/arm/home`
所有 joint 走到零位。需 torque=ON。

### `POST /api/arm/gripper`
夹爪开/合增量(仅当 config 里有 gripper joint)。

---

## 13. 录制 / 回放 (Recording)

内存录制(memory-only),记录所有 cmd 事件(`hold_down`/`hold_up`/`cmd`)+ 10 Hz 传感器快照(ultra / pose / speed)。max 10 分钟自动停。服务重启清空。

### `GET /api/rec/status`
```json
{
  "recording": false, "playing": false,
  "event_count": 42, "total_events": 1023,
  "duration_s": 15.5, "elapsed_s": 0.0,
  "playback": null
}
```
回放中 `playback` 是:
```json
{
  "idx": 15, "total": 42, "elapsed": 7.8, "duration": 15.5,
  "direction": "forward", "speed": 1.0, "calibrate": false,
  "current": {"kind": "hold_down", "action": "forward"},
  "trail_recorded": [...], "trail_replay": [...],
  "ultra_recorded": {...}, "ultra_live": {...}, "ultra_diff_mm": {...}
}
```

### `POST /api/rec/start`/`stop`/`clear`
- `start`:清空已有事件,开始录制 + sensor 快照线程
- `stop`:停止录制
- `clear`:idle 时清空

### `POST /api/rec/play`
```
POST /api/rec/play?direction=forward&speed=1.0&calibrate=0
                  direction: forward / reverse
                  speed: 0.1 - 5.0
                  calibrate: 0/1 → 推送录制 vs 实时 ultra 偏差给 UI
```
**倒放语义**:
- 整体事件逆序 + 每条 cmd 用 `INVERSE` 反映射(forward↔backward, left↔right, rotate_cw↔rotate_ccw, speed_up↔speed_down,音效原样)
- `hold_down` ↔ `hold_up` 互换(反着走时间)
- **事件级 timing 补偿**:倒放后的 backward 段(原 forward)duration ×1/`BACKWARD_RATIO`,forward 段(原 backward)duration ×`BACKWARD_RATIO`,后续事件 shift,让倒放距离匹配原前进。`BACKWARD_RATIO` 默认 0.7,可通过 `/api/map/config` 校准。

### `POST /api/rec/stop_playback`
立即终止当前回放,释放所有 hold + car.stop()。

---

## 14. 速度模型 / 标定 (Speed Model)

`/api/map/config` 接受三个字段,影响 dead-reckoning + distance API + 倒放 timing:

```json
{
  "k_linear": 0.05,        // mm/s per BUTTONSPEED. BS=2000 → 100mm/s
  "k_angular": 0.0006,     // rad/s per BUTTONSPEED. BS=2000 → 1.2 rad/s ≈ 69°/s
  "backward_ratio": 0.7    // 重心前置 → 后驱效率低,vB / vF
}
```

**校准方法**:
- `k_linear`:用 distance API 让车前进 1000mm,量实测距离,反推 k_linear = 实测 / 期望 × 旧值。
- `backward_ratio`:车前进 X mm、然后后退 X mm,看是否回到原点。回不到则 ratio = 实际后退 / 实际前进。或录"直走 3 秒"倒放看回零情况。
- `k_angular`:同 k_linear,用 `?angle=N` 测旋转。
