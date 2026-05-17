# Hunter 软件-硬件联调交接清单

## 当前软件已经完成什么

软件侧已经完成无硬件 MVP 闭环，可以在本地用 mock 输入跑通完整产品链路：

- mock 场景：空场、靠近成功、丢失目标、异常停车。
- app 主循环：health / snapshot / detector / tracker / state machine / motion command。
- session 数据：states、events、summary、report、artifact。
- session history：JSONL 追加式历史存储。
- dashboard preview：近期 session、结果统计、动作统计、里程碑状态。
- daily diary：按 session artifact 聚合日报，支持模板输出和可插拔 LLM 函数。
- memory loop：session 结果可写入 MemoryBox。
- personalization：根据 MemoryBox 偏好推荐下一次玩法 arm。
- software MVP acceptance：一条入口输出软件侧是否 ready for hardware integration。

## 软件侧怎么跑

### 1. 跑基础 mock 场景

```bash
python -m src.app.demo --mode mock --scenario all --include-memory-update
```

这会跑四个验收场景，并输出每个场景的状态、summary、report、memory_update。

### 2. 跑产品层预览

```bash
python -m src.app.demo --product-suite
```

这会输出 dashboard preview、daily diary、personalization preview 等产品层结果。

### 3. 跑软件 MVP 验收

```bash
python -m src.app.demo --software-mvp-acceptance
```

这会输出软件侧 acceptance summary，包括：

- 是否 ready for hardware integration。
- 覆盖了哪些 mock outcome。
- 已完成的软件能力。
- 真实 MVP 还剩哪些硬件集成项。
- 当前 personalization 推荐状态。

### 4. 跑真实硬件模式入口

```bash
python -m src.app.demo --mode real --base-url http://<robot-host>:<port> --ticks 10
```

真实模式会使用 `HunterAPI` 连接硬件服务。硬件服务还没对齐前，这个命令主要用于接口联调，不代表已经能直接安全上车。

## 硬件侧需要提供什么

### HunterAPI 服务

请提供或确认真实机器人服务支持软件调用这些能力：

- health：返回机器人是否健康、是否可控制。
- snapshot：返回当前传感器/机器人状态。
- move forward：前进。
- rotate cw / ccw：左右旋转。
- stop：安全停车。

需要对齐的信息：

- base URL。
- 每个 endpoint 的路径。
- request body 字段。
- response body 字段。
- 错误码和异常返回格式。
- 命令是否同步执行，还是只表示已下发。

### 摄像头 / YOLO 输出

软件 tracker 当前期望 detector 输出 list[dict]，每个 detection 至少包含：

```python
{
    "bbox": (x1, y1, x2, y2),
    "conf": 0.8,
    "cx": 320,
    "cy": 240,
    "w": 160,
    "h": 160,
}
```

如果 YOLO 输出字段不同，需要在接入层转换成这个格式。

### 运动与安全参数

硬件侧需要现场确认：

- forward 命令速度是否安全。
- rotate 命令方向是否和软件语义一致。
- stop 是否立即生效。
- 目标接近时的 stop distance 是否合适。
- 丢失目标后是否保持停车。
- detector/API 异常时是否保持停车。

## 联调顺序建议

1. 只测 health / snapshot，不发运动命令。
2. 单独测 stop，确认任何状态下都可安全停车。
3. 低速测试 rotate_cw / rotate_ccw，确认方向和幅度。
4. 低速测试 forward，确认速度和刹停距离。
5. 接入真实 detector，只打印 detection，不发运动命令。
6. 跑 `--mode real --ticks 1`，确认 app loop 能读真实状态。
7. 小范围跑 real mode，多观察 stop / lost_target / error 行为。
8. 再尝试完整 approach 场景。

## 当前不能承诺的事情

- 不能保证真实硬件 endpoint 已完全匹配。
- 不能保证 YOLO 输出格式已匹配。
- 不能保证运动速度、旋转幅度、停止距离适合现场。
- 不能跳过真实安全测试直接上车。

## 判断联调完成的标准

硬件接入后，至少需要真实跑通这些路径：

- no_target：无猫时保持安全待机。
- success：看到猫后靠近，并在安全距离停车。
- lost_target：中途丢猫后停车。
- error：detector 或 API 异常时停车。

这些真实 session 应该继续进入 summary、report、history、dashboard、diary、memory 和 personalization。只要真实数据能走完这条链路，就可以认为软件-硬件 MVP 完成。
