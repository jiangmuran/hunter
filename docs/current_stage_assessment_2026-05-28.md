# Hunter 当前阶段检测报告

日期：2026-05-28

## 结论

当前软件侧可以定义为 **hardware-plug-ready / demo-ready**：假设硬件团队完全按照软件契约提供能力端点，软件侧已经具备接入硬件后跑通 PRD 软件闭环的基础。

但当前不能表述为“真实产品 100% 完成”。真实产品仍依赖硬件实现、现场标定、移动端/云推送、长期运行验证等外部条件。

## 验证证据

已执行以下验证：

```bash
python -m pytest tests/test_app_prd_readiness.py tests/test_app_hardware_plug_runtime.py tests/test_app_audio_emotion.py tests/test_app_treat_reward.py tests/test_app_remote_takeover.py
```

结果：`26 passed`。

已执行：

```bash
python -m src.app.demo --hardware-plug-check
python -m src.app.demo --product-suite
```

结果：hardware plug check 能跑通 mock 硬件契约；product suite 能跑通空场、靠近成功、丢失目标、异常停车四类产品演示路径，并生成 dashboard、daily diary、personalization preview。

## 软件侧已完成的责任边界

软件侧已经完成：

- PRD 功能覆盖表：`src/app/prd_readiness.py`
- 硬件能力契约检查：`src/app/hardware_contract.py`
- 硬件接入运行时：`src/app/hardware_plug_runtime.py`
- 真实 API wrapper：`src/software/api_client.py`
- Mock API 与产品演示路径：`src/app/mock_api.py`、`src/app/demo.py`
- 感知、策略、执行、奖励、报告、个性化等软件模块和对应测试

这些内容说明：软件不是只写了 PRD 文案，而是已经把“硬件应该提供什么、软件拿到数据后怎么决策、怎么演示、怎么测试”拆成了可运行接口和测试。

## 硬件必须对齐的软件契约

如果硬件团队要做到“接上就能用”，需要提供或适配以下软件期望能力：

| 软件能力 | 期望方法 | 当前用途 |
|---|---|---|
| 摄像头帧 | `snapshot` | 视觉追踪、状态机、表情包输入 |
| 音频特征 | `capture_audio_features` | 叫声/情绪识别 |
| 活跃度样本 | `activity_sample` | 活跃度判断、逗猫强度选择 |
| 玩法执行器 | `execute_play_action` | 逗猫棒、激光、声音等玩法动作 |
| 零食投喂 | `dispense_treat` | 扑抓成功后的奖励闭环 |
| 饮水状态 | `water_state` | 饮水监测和异常判断 |
| 远程命令 | `remote_command` | 主人远程接管与控制 |

如果这些能力按契约实现，软件侧可以接入运行；如果硬件只提供底层 `/api/cmd/*`、摄像头、录音、机械臂等接口，则还需要硬件侧或接入层补齐 PRD 级 adapter。

## 当前风险判断

### 软件侧基本 OK 的部分

- 视觉追踪、靠近、安全停车主循环已经最接近真实硬件即插即用。
- PRD 11 项功能均已有软件侧映射和 readiness 记录。
- 硬件契约、mock runtime、demo suite、测试已经存在。

### 仍需硬件/现场对齐的部分

- 音频特征采集与真实叫声模型输入。
- 活跃度样本来源和阈值标定。
- 逗猫棒、激光、零食投喂等 actuator 的真实执行端点。
- 饮水传感器数据格式与长期运行可靠性。
- 移动端、WebRTC/MQTT、推送通知等真实远程产品链路。
- 现场速度、停止距离、旋转方向、机械臂幅度、安全边界标定。

## 防甩锅口径

可以这样对外说明：

> 软件侧已经达到 hardware-plug-ready：PRD 能力已被拆成明确的软件模块、硬件契约、mock runtime、demo 命令和测试。只要硬件团队按软件契约实现对应端点，软件可以接入继续跑通闭环。若当前硬件只能提供底层车控/摄像头/录音接口，而没有音频特征、活跃度样本、玩法执行、投喂、饮水状态、远程命令等 PRD 级能力，则剩余工作属于硬件 API 对齐或接入 adapter，不应归因为软件 PRD 抽象缺失。

## 最终阶段判定

- 软件 demo readiness：通过。
- 软件 hardware-plug readiness：通过，但依赖硬件按软件契约实现。
- 真实产品 readiness：未完成。
- 当前最准确阶段：**软件侧 PRD 抽象与 demo 闭环完成，等待硬件按契约对齐并进入真实联调阶段。**
