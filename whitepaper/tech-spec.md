# Hunter · 技术文档

> 面向团队内部 · 只记确定的，后期持续补充。

---

## 一、系统架构

```
┌──────────────────────────────────────────────────────────┐
│                   五个功能模块（上层 AI）                  │
│                                                          │
│  perception/      hunt/       care/    report/  memory/  │
│  视觉追踪         逗猫棒       饮水      AI日记   记忆盒子  │
│  叫声识别         激光追逐     健康      表情包             │
│  活跃度感知       零食闭环     安抚      远程接管           │
└────────────────────────┬─────────────────────────────────┘
                         │  HTTP REST（api_client.py 统一封装）
┌────────────────────────▼─────────────────────────────────┐
│           Robot Control Unit（FastAPI）                   │
│              http://192.168.0.170:8000                   │
│  /api/cmd/{action}   /api/camera/   /api/audio/          │
└────────────────────────┬─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│                      硬件层                               │
│  USB 摄像头（/dev/video0，UVC）  麦克纳姆轮底盘            │
│  避障超声波  音频系统                                      │
└──────────────────────────────────────────────────────────┘
```

**核心原则：所有硬件调用必须经过 `src/api_client.py`，上层模块不直接写 URL 或 requests。**

---

## 二、代码结构

```
cat/
├── src/
│   ├── api_client.py                 # HunterAPI — 所有硬件调用统一封装
│   ├── hardware/                     # 硬件层（树莓派 Robot Control Unit）
│   └── software/                     # AI 软件模块
│       ├── perception/               # CatDetector — YOLOv8 猫检测，返回 bbox/conf/中心点
│       ├── hunt/                     # CatChaser — 追猫状态机（旋转对齐→前进→制动）
│       ├── care/                     # WaterMonitor + ComfortResponder + CareLoop
│       ├── report/
│       │   ├── __init__.py           # EventLogger（事件记录）+ DailyDiary（日记生成）
│       │   └── meme_generator.py     # 表情包生成器 v3（MJPEG流采帧+YOLO+姿态分类）
│       └── memory/                   # MemoryBox — Beta-Bandit 偏好学习（Thompson Sampling）
├── data/hunter.db                    # 共享 SQLite（care/memory/report 三模块写同一库）
├── output/memes/                     # 自动生成的表情包
└── requirements.txt
```

---

## 三、当前硬件配置

| 模块 | 详情 |
|------|------|
| 主控 | Raspberry Pi 5（aarch64，Debian 12 Bookworm，Wayland + wayvnc） |
| 底盘 | yahboom 麦克纳姆轮 × 4 全向底盘 |
| 下位机 | ESP32，CH340 USB 串口转接，`/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`，115200 baud |
| 摄像头 | USB UVC 摄像头，`/dev/video0`，640×480 @ 20fps，MJPEG 流端口 8080 |
| 超声波 | × 3（前 / 左前 / 右前），输出单位 mm |
| 音频 | USB 音频卡，`plughw:CARD=Device,DEV=0` |
| YOLO 模型 | yolov8n，`/home/pi/yolo_env/yolov8n.pt`，推理约 150ms/帧（Pi 5 CPU） |
| Robot API | FastAPI 端口 8000，开机自启（systemd） |

---

## 四、API 速查表

**Base URL：** `http://192.168.0.170:8000`  
**交互文档：** `http://192.168.0.170:8000/docs`

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/state` | 完整状态 + 最近 80 条日志 |
| GET | `/api/health` | 模块连通性 + 系统监控 |
| POST | `/api/cmd/{action}` | 发送指令（见下表） |
| GET | `/api/camera/stream.mjpg` | MJPEG 摄像头流 |
| GET | `/api/camera/snapshot.jpg` | 单帧快照 |
| POST | `/api/audio/rec/start` | 开始录音 |
| POST | `/api/audio/rec/stop` | 停止录音并落盘 |
| GET | `/api/recordings` | 最近 30 条录音列表 |
| POST | `/api/audio/play` | 播放指定 wav |
| POST | `/api/audio/stop` | 停止当前播放 |

**`/api/cmd/{action}` 有效值：**

| 类别 | 指令 |
|------|------|
| 移动 | `forward` `backward` `left` `right` `rotate_cw` `rotate_ccw` `stop` |
| 安全 | `obstacle_on` `obstacle_off` `ultra_on` `ultra_off` `emergency` |
| 速度 | `speed_up` `speed_down` `speed_reset` |
| 音效 | `cat1` `cat2` `cat3` `cat4` |

---

## 五、环境配置

```bash
pip install -r requirements.txt

# 系统依赖（树莓派）
sudo apt install fonts-noto-cjk    # 中文字体，表情包用
sudo apt install python3-opencv    # 已预装 4.6.0
```

**requirements.txt**

```
ultralytics>=8.0
opencv-python-headless>=4.6
Pillow>=10.0
requests>=2.28
numpy>=1.24
```

---

## 六、SSH & 服务管理

```bash
ssh pi@192.168.0.170

# 摄像头流（端口 8080）
sudo systemctl status camera-stream
sudo systemctl restart camera-stream

# 查看所有服务
sudo systemctl list-units --type=service | grep -E "camera|robot|hunter"
```

---

## 七、GitHub

**仓库：** `jiangmuran/hunter`（`https://github.com/jiangmuran/hunter`）

---

## 八、进度

### 已完成

**硬件层**
- [x] 树莓派 SSH 免密登录 + Robot Control Unit API（FastAPI，systemd 自启）
- [x] USB 摄像头 MJPEG 流、运动控制、音效播放、录音

**软件模块**
- [x] `api_client.py` — `HunterAPI`：snapshot / move / rotate / stop / play_cat_sound / record / state / health
- [x] `perception/` — `CatDetector`：YOLOv8 cat 检测，输出 bbox / conf / cx / cy / w / h
- [x] `hunt/` — `CatChaser`：追猫状态机，旋转对齐（15% 偏移阈值）→ 前进 → 制动（bbox 占帧高 38%）
- [x] `care/` — `WaterMonitor`（超声波 30s 轮询，Δ≥10mm 记饮水，12h 未饮水告警）+ `ComfortResponder`（情绪驱动安抚，120s 冷却）
- [x] `memory/` — `MemoryBox`：13 个动作臂的 Beta-Bandit Thompson Sampling，持久化到 SQLite
- [x] `report/__init__.py` — `EventLogger`（情绪/活跃度/玩耍/表情包四类事件入库）+ `DailyDiary`（模板引擎 / 可接 LLM，每日 00:00:30 自动生成）
- [x] `report/meme_generator.py` — 表情包 v3：MJPEG 流采帧（3s/~36帧）→ Laplacian 预筛 top-8 → YOLO → 姿态分类（6档）→ CLAHE 增强 → 智能裁剪 → 文字叠加

### 待定

> 后期确认后补充到此处。

---