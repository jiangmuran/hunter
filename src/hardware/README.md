# `src/hardware/` — 树莓派机器人控制层

Hunter 跑在一台树莓派 5 + yahboom 3WD 全向轮小车上。这一层把所有硬件能力(运动、超声波避障、麦克风/喇叭、USB 摄像头、YOLO 实时检测、风扇温控)统一封装成 **一个 FastAPI / WebSocket 服务**,给上层 AI / 业务程序调用。

启动后访问 `http://<pi-ip>:8000` 即可看到带 HUD 的控制台;接口和 WebSocket 协议见 [`API.md`](./API.md)。

---

## 文件

| 文件 | 作用 |
|---|---|
| `web_console.py` | 单文件 FastAPI 服务,包含全部 REST/WS 端点 + 内嵌 HTML 控制台 |
| `car_driver.py` | yahboom ESP32 串口驱动(全向轮 / 避障 / 超声波 / 速度 / 猫叫 / PS3 回调)|
| `audio_driver.py` | USB 音频(`aplay` / `arecord`)封装:播放 / 固定录音 / 按住录音 / 实时麦克风流 |
| `API.md` | HTTP / WebSocket 接口完整参考,含 curl + Python 示例 |
| `systemd/robot-console.service` | 系统级服务单元:开机自启 web 控制台 |
| `systemd/fan-curve.service` | 一次性服务:启动时把 RPi 5 cooling-fan trip 点改成激进档(40/45/55/65°C)|

---

## 硬件接线 (RPi 5)

- ESP32 经 CH340 USB Serial 接 RPi USB-A,Linux 自动识别为 `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`
- USB 摄像头(UVC, 任意品牌)接 USB-A,出现在 `/dev/video0`
- USB 音频卡接 USB-A,`aplay -l` 看到 `plughw:CARD=Device,DEV=0`
- 风扇接主板 4-pin **FAN** 接口(`/boot/firmware/config.txt` 加 `dtparam=cooling_fan=on` 后温控启用)

---

## 软件依赖

跑在 RPi OS Bookworm,Python 3.11 + 一个 venv:

```bash
# venv 已经在用户机器上预装(YOLO 提供)
ls /home/pi/yolo_env/bin/python3

# 补装 web 框架到 venv:
/home/pi/yolo_env/bin/pip install fastapi 'uvicorn[standard]' pyserial
# (ultralytics / torch / opencv-python / numpy 由 YOLO 安装步骤提供)
```

可选(没有 ultralytics 也能跑,YOLO 自动 disabled):
```bash
/home/pi/yolo_env/bin/pip install ultralytics
```

---

## 部署 (一次性)

```bash
# 1. 文件就位
cp web_console.py    /home/pi/Desktop/
cp car_driver.py     /home/pi/Desktop/
cp audio_driver.py   /home/pi/Desktop/

# 2. 装服务单元
sudo cp systemd/robot-console.service /etc/systemd/system/
sudo cp systemd/fan-curve.service     /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now robot-console fan-curve
```

之后浏览器打开 `http://<pi-ip>:8000`。

服务管理:
```bash
sudo systemctl status   robot-console
sudo systemctl restart  robot-console
journalctl -u robot-console -f
```

---

## YOLO

服务首次启动后 YOLO 默认 disabled。在网页右上 `YOLO` 按钮启用,或者:
```bash
curl -X POST -H 'Content-Type: application/json' \
     -d '{"enabled":true, "classes":[0,63]}' \
     http://<pi-ip>:8000/api/yolo/config
```
配置(开关 / conf / imgsz / rate / classes)自动持久化到 `~/.config/robot-console/yolo_state.json`,重启自动恢复。

YOLOv8n 在 RPi 5 CPU 上 `imgsz=416` 推理约 **150-200ms / 帧 ≈ 5-7 FPS**,够画 bbox 叠加到实时画面上。

---

## 接口速查

| 用途 | 端点 |
|---|---|
| 完整状态 | `GET /api/state` |
| 模块健康 | `GET /api/health` |
| 控制车 | `POST /api/cmd/{action}` (forward/backward/left/right/rotate_*/stop/obstacle_*/ultra_*/emergency/speed_*/cat1-4) |
| 音频录音/播放 | `POST /api/audio/rec/{start,stop}` / `POST /api/audio/play` |
| 摄像头 | `GET /api/camera/stream.mjpg` / `GET /api/camera/snapshot.jpg` |
| YOLO | `GET /api/yolo/status` / `POST /api/yolo/config` / `GET /api/yolo/models` |
| 实时推送 | `WS /ws` |

完整参数 / 返回结构 / 示例代码见 [`API.md`](./API.md) 或访问运行中的 `/docs` (Swagger UI)。
