# Hunter · 技术与执行文档

> 面向自己和团队 · V1.0 · 2026年5月
> 语言以清晰、可执行为目标，不写废话。

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
│  /api/state          [ /api/arm/ → ] [ /api/yolo/ → ]   │
└────────────────────────┬─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│                      硬件层                               │
│  USB 摄像头（/dev/video0，UVC）  麦克纳姆轮底盘            │
│  避障超声波  音频系统  [ 机械臂 → ]  [ 投食机构 → ]        │
└──────────────────────────────────────────────────────────┘
```

**核心原则：所有硬件调用必须经过 `src/api_client.py`，上层模块不直接写 URL 或 requests。**

---

## 二、代码结构

```
cat/
├── README.md                    # 项目主页（对外展示）
├── WHITEPAPER.md                # 白皮书入口
├── whitepaper/
│   ├── 对外-项目介绍.md          # 本文件的对外版本
│   └── 对内-技术与执行文档.md    # 本文件
├── requirements.txt
├── output/
│   └── memes/                   # 自动生成的表情包存放位置
└── src/
    ├── api_client.py            # ✅ 硬件 API 统一入口
    ├── perception/              # 第一步：感知
    │   ├── cat_tracker.py       ⬜ 视觉追踪 + 状态判断
    │   └── sound_classifier.py  ⬜ 叫声分类
    ├── hunt/                    # 第二步：狩猎
    │   └── motion_generator.py  ⬜ 惊喜熵 + 生成式动作
    ├── care/                    # 第三步：照料
    │   └── health_monitor.py    ⬜ 饮水/步态异常检测
    ├── report/                  # 第四步：汇报
    │   ├── meme_generator.py    ✅ 已完成
    │   └── daily_diary.py       ⬜ LLM 生成日记
    └── memory/                  # 记忆盒子
        └── memory_box.py        ⬜ 长期记忆存储
```

---

## 三、当前硬件配置

### 现在用的（开发 & MVP）

| 模块 | 详情 |
|------|------|
| 主控 | Raspberry Pi 5（aarch64，6.12 内核） |
| 系统 | Debian 12 Bookworm |
| 桌面 | Wayland + wayvnc |
| 摄像头 | USB 2.0 HD Camera，`/dev/video0`，UVC 驱动 |
| 流分辨率 | 1280×720 @ 30fps |
| Robot API | FastAPI，端口 8000，开机自启 |
| 摄像头流 | MJPEG HTTP，端口 8080，systemd 管理 |

### 最终目标硬件（方案 V1）

| 模块 | 选型 | 备注 |
|------|------|------|
| 主控 | Jetson Orin Nano 8GB | 跑 YOLO + 轻量 VLM |
| 机械臂 | 4-5 DoF，LeRobot SO-100 改装 | 嘉立创打样结构件 |
| 底盘 | 麦克纳姆轮 × 4 全向 | 自研驱动板走嘉立创 PCB |
| 摄像头 | 主摄 IMX219 广角 + 腕部 OV5647 | 双路 |
| 投食 | 单轴舵机 + 螺旋送料 | 3D 打印 |
| 力传感 | 关节电流环检测（无独立传感器） | 软件实现 |
| 电池 | 5000mAh 锂电，主动 4h，自动回桩 | 自研充电桩 PCB |
| 外壳 | PETG 3D 打印 + LED 面板 | 嘉立创 3D 打印服务 |
| **BOM** | **¥1800–2400，量产可压至 ¥1200** | |
| **零售** | **¥1999** | 0 利润 + 订阅赚 LTV |

---

## 四、API 速查表

**Base URL：** `http://192.168.0.170:8000`
**交互文档：** `http://192.168.0.170:8000/docs`

### 端点列表

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/state` | 完整状态 + 最近 80 条日志 |
| GET | `/api/health` | 模块连通性 + 系统监控 |
| POST | `/api/cmd/{action}` | 发送指令（见下表） |
| GET | `/api/camera/stream.mjpg` | MJPEG 摄像头流（持续） |
| GET | `/api/camera/snapshot.jpg` | 单帧快照（一次性） |
| POST | `/api/audio/rec/start` | 开始录音，写入 wav |
| POST | `/api/audio/rec/stop` | 停止录音并落盘 |
| GET | `/api/recordings` | 最近 30 条录音列表（按 mtime 降序） |
| POST | `/api/audio/play` | 播放指定 wav |
| POST | `/api/audio/stop` | 停止当前播放 |

### `/api/cmd/{action}` 有效值

| 类别 | 指令 |
|------|------|
| 移动 | `forward` `backward` `left` `right` `rotate_cw` `rotate_ccw` `stop` |
| 安全 | `obstacle_on` `obstacle_off` `ultra_on` `ultra_off` `emergency` |
| 速度 | `speed_up` `speed_down` `speed_reset` |
| 音效 | `cat1` `cat2` `cat3` `cat4` |

---

## 五、环境配置

### 依赖安装

```bash
# Python 依赖
pip install -r requirements.txt

# 系统依赖（树莓派）
sudo apt install fonts-noto-cjk       # 中文字体，表情包用
sudo apt install python3-opencv        # 已预装 4.6.0
```

### requirements.txt

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
# 免密登录（ED25519 密钥已配置）
ssh pi@192.168.0.170
# 密钥指纹：SHA256:WWGjpJfvft/ypAd5l9EqR6ut7iPcxCWoAC6XdUAyKZY

# 摄像头 MJPEG 流（端口 8080）
sudo systemctl status camera-stream
sudo systemctl restart camera-stream
journalctl -u camera-stream -f

# 查看所有服务
sudo systemctl list-units --type=service | grep -E "camera|robot|hunter"
```

---

## 七、GitHub

**仓库：** `jiangmuran/hunter`（`https://github.com/jiangmuran/hunter`）
**提交账号：** jiangmuran

```bash
git add .
git commit -m "描述变更内容"
git push origin main
```

---

## 八、48 小时 MVP 执行计划

> 目标：在比赛现场跑通两个核心演示——①机械臂对评委手部的实时躲闪；②AI 日记样例展示。

### Day 1（前 24 小时）—— 跑通 P0 核心

| 时段 | 任务 | 负责 | 验收标准 |
|------|------|------|---------|
| 00–04h | `cat_tracker.py`：YOLOv8 nano 接入，实时检测猫/手 | 算法 | 终端输出 bbox + 置信度，延迟 <200ms |
| 04–08h | 状态机：检测到目标 → 底盘靠近 → 触发音效 | 算法+硬件 | 小车能跟踪目标移动，遇障停止 |
| 08–12h | 惊喜熵原型：生成 3 种不可预测逃避动作 | 算法 | 同一方向追逐时，底盘至少切换 2 次方向 |
| 12–16h | 评委互动演示流程打磨：伸手 → 躲开 → 音效 | 全员 | 完整流程无卡顿，可稳定复现 |
| 16–20h | 运动仪表盘：轨迹热力图 + 互动时长统计 | 前端 | 浏览器可访问，数据实时更新 |
| 20–24h | 综合联调 + Bug 修复 | 全员 | P0 演示流程完整跑通，备份一份录制视频 |

### Day 2（后 24 小时）—— 加分项 + 备战

| 时段 | 任务 | 负责 | 验收标准 |
|------|------|------|---------|
| 00–06h | `daily_diary.py`：接 LLM API，生成示例日记 | 算法 | 产出一篇真实感强的中文日记样本 |
| 06–10h | 表情包演示素材准备：提前生成 10 张备用 | 算法 | 表情包文字居中，无乱码，有效情感文案 |
| 10–14h | 备用方案制作：完整演示录制视频（防现场故障） | 全员 | 4–5 分钟剪辑，含躲闪 + 日记 + 表情包 |
| 14–18h | 路演 Pitch 演练 × 3 轮 | 全员 | 控制在 8 分钟内，金句自然，不看稿 |
| 18–22h | 设备运输检查 / 充电 / 备件清单核对 | 硬件 | 全部设备电量满，备用 U 盘含离线模型 |
| 22–24h | 休息 / 最终 Q&A 预演 | 全员 | 准备好 10 个高频追问的回答 |

### 高频追问预演

| 问题 | 标准回答要点 |
|------|------------|
| 为什么不做狗？ | V1 聚焦猫，狗运动尺度差异大，做不好两边都伤 |
| 惊喜熵怎么量化？ | 实时计算当前动作的信息熵，目标维持 0.7 确定性区间 |
| 猫不配合怎么办？ | 状态机检测活跃度 <阈值时自动暂停，不强迫互动 |
| BOM 成本压缩路径？ | 嘉立创量产 PCB + 结构件，量产可从 ¥2200 压至 ¥1200 |
| 数据隐私怎么保证？ | 视觉数据本地处理，仅上传非视频摘要数据 |

---

## 九、开发路线图

### ✅ 已完成

- [x] 树莓派 SSH 免密登录（ED25519）
- [x] USB 摄像头 MJPEG 流（VNC / 浏览器实时查看，systemd 自启）
- [x] Robot Control Unit API 接入（移动 / 摄像头 / 音效 / 录音）
- [x] 项目代码结构（五模块）
- [x] `src/api_client.py` 统一封装
- [x] `src/report/meme_generator.py` 表情包生成器

### ⬜ P0 — MVP 演示必须有（优先级最高）

- [ ] `perception/cat_tracker.py`：YOLOv8 nano 检测猫 + 实时位置追踪
- [ ] 状态机：检测到猫 → 底盘移动靠近 → 触发音效 / 互动
- [ ] 运动仪表盘：轨迹热力图 + 互动时长 / 冲刺次数统计

### ⬜ P1 — 比赛加分项

- [ ] `hunt/motion_generator.py`：惊喜熵引擎，生成式非重复动作
- [ ] `report/daily_diary.py`：接 LLM API，每日生成文字日记
- [ ] `care/health_monitor.py`：饮水异常检测 + 预警推送

### ⬜ P2 — 产品完整性

- [ ] `perception/sound_classifier.py`：叫声分类（饥饿 / 撒娇 / 警戒）
- [ ] 远程 App 接管（第一视角实时流 + 指令转发）
- [ ] 机械臂 API 扩展接入（`/api/arm/`）
- [ ] 投食机构联动（扑中触发）
- [ ] `memory/memory_box.py`：长期互动记录 + 结构化存储

---

## 十、提交材料清单

> 比赛提交截止前逐项核对，✅ 表示已就绪。

| 材料 | 要求 | 状态 |
|------|------|------|
| 演示视频 | 3–5 分钟，展示核心交互 + AI 日记 | ⬜ |
| 源代码仓库 | GitHub 公开，README 完整 | ⬜ |
| 白皮书 / 项目介绍 PPT | 含商业逻辑、技术深度、团队介绍 | ⬜ |
| 现场 Demo 硬件 | 底盘 + 树莓派 + 摄像头，调试完毕 | ⬜ |
| 备用演示录制 | 防现场故障，存 U 盘离线播放 | ⬜ |
| 离线模型文件 | yolov8n.pt，防网络不稳定 | ⬜ |
| 充电设备 / 备件 | 移动电源、备用摄像头线、螺丝刀 | ⬜ |

---

## 十一、竞赛合规检查

| 检查项 | 说明 | 状态 |
|--------|------|------|
| 代码原创声明 | 所有核心算法自研，第三方库已标注 | ⬜ |
| 开源协议合规 | ultralytics (AGPL)、Pillow (HPND)、FastAPI (MIT) | ⬜ |
| 数据采集合规 | 视觉数据本地处理，不上传原始视频 | ⬜ |
| 团队成员实名 | 提交材料与参赛报名信息一致 | ⬜ |
| 演示设备安全 | 机械臂力矩限制，无尖锐裸露部件 | ⬜ |

---

## 十二、已知问题与决策记录

| 问题 | 决策 | 原因 |
|------|------|------|
| wayvnc 使用 Wayland，X11 窗口无法直接显示 | MJPEG HTTP 流代替 OpenCV 窗口 | 浏览器原生支持，任意设备可访问 |
| Pi 5 无 `/dev/vchiq`，旧版 picamera 不可用 | 用 picamera2 / UVC（v4l2） | Pi 5 改用 libcamera 栈 |
| Windows 上 sshpass 不可用 | 用 Python paramiko 推送公钥 | paramiko 跨平台，无需额外安装 |
| git 本地分支 master vs 远程 main | 重命名为 main，设置 upstream | 与 GitHub 默认分支对齐 |
| WebFetch 无法访问局域网 IP | 用 PowerShell `System.Net.WebClient` 拉取 API | WebFetch 走外网，访问不到 192.168.x.x |
| YOLO 在 Pi 5 推理延迟 | 优先使用 yolov8n（nano），降级时切换纯运动检测 | Pi 5 无 GPU，nano 模型约 150ms/帧可接受 |
