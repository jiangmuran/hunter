# Hunter 硬件侧交付需求文档

日期：2026-05-28

## 目的

本文档定义硬件团队需要交付的能力边界。软件侧已经完成 PRD 软件闭环、mock demo、硬件契约检查和产品级运行入口；硬件侧需要按本文档提供稳定接口，才能进入真实联调并验证“接上硬件即可运行”。

本文档的目标不是要求硬件实现软件内部逻辑，而是要求硬件提供软件运行所需的输入数据和执行能力。

## 总体交付原则

硬件侧必须提供一组稳定、可调用、可测试的 HTTP API 或等价 adapter，使软件侧可以通过统一 `HunterAPI` 调用以下能力：

- 摄像头帧输入
- 音频特征输入
- 活跃度样本输入
- 玩法动作执行
- 零食投喂执行
- 饮水状态读取
- 远程控制命令执行
- 健康状态与安全停车

所有接口必须明确：

- endpoint 路径
- HTTP 方法
- request body / query 参数
- response body 字段
- 单位
- 错误码 / 异常格式
- 超时行为
- 是否同步完成
- 安全失败策略

## 必须交付的接口能力

### 1. 健康状态接口

软件期望方法：`health()`

建议接口：

```http
GET /api/health
```

必须返回：

```json
{
  "ok": true,
  "modules": {
    "camera": {"ok": true},
    "audio": {"ok": true},
    "motion": {"ok": true},
    "arm": {"ok": true},
    "water": {"ok": true},
    "reward": {"ok": true}
  }
}
```

要求：

- `ok=false` 时软件会进入保守停车或禁用对应能力。
- 字段可以扩展，但不得破坏已有字段含义。
- 模块不可用时必须明确说明，不要返回假成功。

### 2. 摄像头快照接口

软件期望方法：`snapshot()`

当前已有接口：

```http
GET /api/camera/snapshot.jpg
```

要求：

- 返回最新 JPEG 帧。
- 帧应可被 OpenCV 解码。
- 建议分辨率：640×480 或明确告知实际尺寸。
- 摄像头不可用时返回明确错误，不返回空图或损坏图片。

用途：

- 视觉追踪
- 猫咪检测
- 表情包生成
- 会话记录

### 3. 音频特征接口

软件期望方法：`capture_audio_features()`

必须提供：

```http
GET /api/audio/features
```

建议返回：

```json
{
  "pitch_hz": 650,
  "energy": 0.62,
  "duration_ms": 900,
  "repetition": 2,
  "noise_floor_db": -40,
  "source": "microphone"
}
```

字段要求：

- `pitch_hz`：主频，单位 Hz。
- `energy`：归一化能量，范围建议 0–1。
- `duration_ms`：声音片段持续时间，单位 ms。
- `repetition`：短时间内重复叫声次数。

用途：

- 软件侧根据特征分类饥饿、撒娇、警戒、满足等情绪。

硬件侧责任：

- 提供稳定音频采集。
- 做基础降噪或至少提供可用特征。
- 明确静默时返回格式。

### 4. 活跃度样本接口

软件期望方法：`activity_sample()`

必须提供：

```http
GET /api/activity/sample
```

建议返回：

```json
{
  "motion_score": 0.68,
  "visible": true,
  "sample_window_seconds": 10,
  "source": "camera_or_imu"
}
```

字段要求：

- `motion_score`：运动强度，范围建议 0–1。
- `visible`：猫咪或目标是否可见。
- `sample_window_seconds`：采样窗口秒数。
- `source`：数据来源，例如 camera、imu、optical_flow。

用途：

- 判断猫咪当前活跃度。
- 决定逗猫强度。
- 避免在低活跃或休眠状态下强刺激。

### 5. 玩法动作执行接口

软件期望方法：`execute_play_action(action, intensity, duration_ms)`

必须提供：

```http
POST /api/play/action
```

请求示例：

```json
{
  "action": "wand_fast",
  "intensity": "medium",
  "duration_ms": 1200
}
```

建议支持的 action：

- `wand_slow`
- `wand_fast`
- `wand_hover`
- `laser_escape`
- `laser_zigzag`
- `sound_tease`

响应示例：

```json
{
  "ok": true,
  "action": "wand_fast",
  "intensity": "medium",
  "duration_ms": 1200,
  "executed": true
}
```

要求：

- 所有动作必须有安全边界。
- `duration_ms` 到时必须自动停止或回到安全姿态。
- 不支持的 action 必须返回明确错误。
- 禁止接口返回成功但硬件没有执行。

用途：

- 逗猫棒挥舞
- 激光追逐
- 声音诱导
- 惊喜熵动作执行

### 6. 零食投喂接口

软件期望方法：`dispense_treat(grams, reason)`

必须提供：

```http
POST /api/reward/treat
```

请求示例：

```json
{
  "grams": 1.0,
  "reason": "catch_success"
}
```

响应示例：

```json
{
  "ok": true,
  "dispensed": true,
  "grams": 1.0,
  "remaining_estimate": 32
}
```

要求：

- 必须支持投喂成功 / 失败的明确返回。
- 必须说明投喂单位是 grams、粒数，还是其他单位。
- 缺粮、卡粮、设备不可用时不得返回成功。
- 建议提供剩余量估计。

用途：

- 猫咪扑抓成功后的奖励闭环。
- 防止过量投喂。

### 7. 饮水状态接口

软件期望方法：`water_state()`

必须提供：

```http
GET /api/water/state
```

建议返回：

```json
{
  "level_mm": 42,
  "last_drink_minutes_ago": 90,
  "sensor_ok": true,
  "daily_delta_ml": 35,
  "updated_at": "2026-05-28T12:00:00Z"
}
```

字段要求：

- `level_mm`：当前液位或距离，单位 mm。
- `last_drink_minutes_ago`：距上次饮水估计时间。
- `sensor_ok`：传感器是否正常。
- `daily_delta_ml`：当日估算饮水量，可选但推荐。

用途：

- 饮水监测。
- 12 小时未饮水告警。
- 日报健康数据。

### 8. 远程命令接口

软件期望方法：`remote_command(command, **params)`

必须提供：

```http
POST /api/remote/command
```

请求示例：

```json
{
  "command": "stop"
}
```

建议支持命令：

- `forward`
- `rotate_cw`
- `rotate_ccw`
- `stop`
- `emergency`
- `play_sound`

响应示例：

```json
{
  "ok": true,
  "command": "stop",
  "executed": true
}
```

要求：

- `stop` 和 `emergency` 必须最高优先级。
- 未授权或不支持的 command 必须明确返回失败。
- 远程命令不得绕过安全限制。

用途：

- 主人远程接管。
- Demo 控制。
- 异常状态下人工介入。

## 安全要求

硬件侧必须保证：

1. `stop` 在任何状态下可调用并尽快生效。
2. `emergency` 在任何状态下可调用并进入急停状态。
3. 摄像头、音频、传感器异常时不得继续执行危险动作。
4. 所有持续动作必须有最大时长限制。
5. 机械臂、激光、投喂、电机动作必须有物理安全边界。
6. API 返回失败时必须明确，不得吞错。

## 联调验收脚本目标

硬件交付后，软件侧会用以下能力做验收：

```bash
python -m src.app.demo --hardware-plug-check
```

通过标准：

- `contract_ready` 为 `True`
- 无 missing capability
- 能返回 activity、audio_emotion、play、water、contract 结果

还会跑：

```bash
python -m src.app.demo --mode real --base-url http://<robot-host>:<port> --ticks 10
```

通过标准：

- 能读取真实 health。
- 能读取真实 snapshot。
- 能进入 app loop。
- 无猫时保持 stop。
- 识别异常时安全停车。

## PRD 功能对应关系

| PRD 功能 | 硬件侧需要提供 |
|---|---|
| 视觉追踪 | 摄像头快照、健康状态、底盘 move/rotate/stop |
| 叫声识别 | 音频特征接口 |
| 活跃度感知 | 活跃度样本接口 |
| 逗猫棒挥舞 | 玩法动作执行接口，机械臂安全执行 |
| 激光点追逐 | 玩法动作执行接口，激光安全角度控制 |
| 零食投喂奖励 | 投喂接口，余量/失败状态 |
| 饮水监测 | 饮水状态接口 |
| 表情包生成器 | 摄像头快照或视频流稳定输入 |
| 猫咪每日日报 | 真实事件源：互动、饮水、情绪、表情包 |
| 猫咪个性偏好 | 真实动作结果、成功/失败反馈 |
| 远程 APP 控制 | 远程命令执行、安全停车、视频/状态通道 |

## 硬件侧交付物清单

硬件团队至少需要交付：

1. 最新 API 文档。
2. 每个接口的 curl 示例。
3. 每个接口的成功 / 失败 response 示例。
4. 真实设备 base URL。
5. 已实现能力清单。
6. 未实现能力清单。
7. 安全限制说明。
8. 标定参数说明。
9. 已知问题列表。
10. 可联调时间和负责人。

## 阻塞判定

以下情况应判定为硬件侧或接口对齐阻塞：

- 缺少 `/api/audio/features`。
- 缺少 `/api/activity/sample`。
- 缺少 `/api/play/action`。
- 缺少 `/api/reward/treat`。
- 缺少 `/api/water/state`。
- 缺少 `/api/remote/command`。
- 接口返回字段单位不明。
- 接口返回成功但硬件未执行。
- `stop` / `emergency` 不能稳定生效。
- 摄像头帧无法被软件解码。
- 传感器异常时没有明确错误返回。

## 软件侧当前状态

软件侧已经提供：

- `src/app/hardware_contract.py`：硬件能力契约检查。
- `src/app/hardware_plug_runtime.py`：硬件接入运行时。
- `src/software/api_client.py`：真实硬件 API wrapper。
- `src/app/mock_api.py`：mock 硬件实现。
- `src/app/demo.py`：demo 和验收入口。
- `src/app/prd_readiness.py`：PRD 软件覆盖状态。
- `docs/current_stage_assessment_2026-05-28.md`：当前阶段检测报告。

因此，后续联调重点是硬件侧按本文档补齐能力端点或提供等价 adapter。
