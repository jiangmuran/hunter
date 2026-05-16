# Hunter · 系统流程与伪代码

> 面向开发者 · V1.0 · 2026年5月
> 本文档描述系统完整工作流程及各模块伪代码，供实现参考。

---

## 一、整体工作流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                        系统启动 / 开机自检                        │
│  检查摄像头连通性 → 检查 API 心跳 → 加载猫咪个性档案 → 进入主循环  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │      主循环（持续）   │
                    │    每 100ms tick 一次 │
                    └──────────┬──────────┘
                               │
              ┌────────────────▼────────────────┐
              │           感知层（并行）           │
              │                                  │
              │  ┌─────────────┐  ┌───────────┐  │
              │  │  视觉追踪    │  │  叫声识别  │  │
              │  │  摄像头帧    │  │  麦克风流  │  │
              │  │  → YOLO检测  │  │  → CNN分类 │  │
              │  │  → 猫咪位置  │  │  → 情绪标签│  │
              │  │  → 活跃度评分│  │            │  │
              │  └──────┬──────┘  └─────┬─────┘  │
              └─────────┼───────────────┼─────────┘
                        └───────┬───────┘
                                │ 融合感知数据
                    ┌───────────▼───────────┐
                    │      状态机决策         │
                    │  输入：位置+情绪+活跃度  │
                    └───────────┬───────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
    ┌─────▼──────┐       ┌──────▼──────┐      ┌──────▼──────┐
    │ 情绪=警戒   │       │ 活跃度 < 20 │      │ 活跃度 ≥ 20 │
    │ 或=饥饿    │       │ 猫咪休眠/静止│      │ 猫咪活跃     │
    └─────┬──────┘       └──────┬──────┘      └──────┬──────┘
          │                     │                     │
    ┌─────▼──────┐       ┌──────▼──────┐      ┌──────▼──────┐
    │ 安全响应    │       │  低功耗等待  │      │  启动逗猫   │
    │ 后退+停止  │       │  5min后重检  │      │  会话流程   │
    │ 推送主人   │       └─────────────┘      └──────┬──────┘
    └────────────┘                                    │
                                                      │
                          ┌───────────────────────────▼──────────────────────────┐
                          │                     逗猫会话                           │
                          │                                                       │
                          │  ① 惊喜熵引擎生成下一个动作                            │
                          │         ↓                                             │
                          │  ② 底盘跟随猫咪移动（PID控制）                         │
                          │         ↓                                             │
                          │  ③ 机械臂执行动作（逗猫棒 / 激光切换）                  │
                          │         ↓                                             │
                          │  ④ 实时检测：猫咪是否扑中？                            │
                          │     ├── 是 → 投食 + 记录成功事件 + 惊喜熵更新          │
                          │     └── 否 → 继续，累计失败次数                        │
                          │         ↓                                             │
                          │  ⑤ 高光帧检测（萌度评分 > 0.7）                        │
                          │         ↓                                             │
                          │  ⑥ 会话结束判定（超时 / 猫咪离开 / 活跃度骤降）         │
                          └───────────────────────────┬──────────────────────────┘
                                                       │
                          ┌───────────────────────────▼──────────────────────────┐
                          │                    后处理（异步）                      │
                          │                                                       │
                          │  ┌─────────────────┐    ┌─────────────────────────┐  │
                          │  │   表情包生成     │    │    记忆更新              │  │
                          │  │  高光帧 → VLM配文│    │  会话数据 → 个性档案     │  │
                          │  │  → Pillow合成    │    │  → Bandit模型增量学习    │  │
                          │  │  → APP推送      │    │  → SQLite 持久化         │  │
                          │  └─────────────────┘    └─────────────────────────┘  │
                          └──────────────────────────────────────────────────────┘
                                                       │
                          ┌───────────────────────────▼──────────────────────────┐
                          │                照料层（并行后台线程）                   │
                          │                                                       │
                          │  饮水监测线程：每30s采样液位 → 异常判定 → 推送预警       │
                          │  健康日报线程：20:00定时 → 聚合数据 → LLM生成 → 推送    │
                          └──────────────────────────────────────────────────────┘
```

---

## 二、整体主循环伪代码

```python
# ═══════════════════════════════════════════════════════
# Hunter 主控程序入口
# ═══════════════════════════════════════════════════════

function main():

    # ── 初始化 ─────────────────────────────────────────
    api         = HunterAPI()                   # 连接机器人硬件
    yolo        = load_model("yolov8n.pt")      # 加载视觉检测模型
    sound_clf   = load_model("sound_cnn.pt")    # 加载叫声分类模型
    profile     = load_cat_profile()            # 加载猫咪个性档案（SQLite）
    entropy_eng = SurpriseEntropyEngine(profile)# 惊喜熵引擎（读取历史偏好）

    # ── 启动并行后台服务 ────────────────────────────────
    start_thread(water_monitor_loop, api)       # 饮水监测线程
    start_thread(daily_report_loop, profile)    # 日报生成线程
    start_thread(audio_stream_loop, sound_clf)  # 叫声识别线程

    # ── 系统自检 ────────────────────────────────────────
    if not api.health().ok:
        alert("硬件连接失败，请检查机器人网络")
        exit()

    # ── 主循环 ──────────────────────────────────────────
    state = IDLE
    while True:

        # 1. 视觉感知
        frame        = api.snapshot()
        cat_bbox     = detect_cat(yolo, frame)           # 返回 bbox 或 None
        activity     = compute_activity(frame, prev_frame)# 光流活跃度 0-100
        emotion      = get_latest_emotion()              # 叫声线程共享变量

        # 2. 状态机转移
        state = transition(state, cat_bbox, activity, emotion)

        # 3. 执行当前状态动作
        if state == IDLE:
            api.stop()

        elif state == ALERT:
            api.move("backward")
            api.stop()
            notify_owner("猫咪发出警戒声，已停止互动")

        elif state == HUNGRY:
            notify_owner("猫咪在叫，可能需要喂食")
            state = IDLE

        elif state == HUNTING:
            session_result = run_hunt_session(api, yolo, entropy_eng, frame, cat_bbox)
            profile.update(session_result)              # 增量更新个性档案
            async_generate_meme(session_result.highlights)

        # 4. 帧缓冲滚动
        prev_frame = frame
        sleep(0.1)  # 100ms tick


# ── 状态转移函数 ────────────────────────────────────────
function transition(state, cat_bbox, activity, emotion):

    if emotion == ALERT:
        return ALERT                        # 最高优先级：警戒立即响应

    if emotion == HUNGRY:
        return HUNGRY

    if cat_bbox is None:
        return IDLE                         # 视野内无猫，待机

    if activity < 20:
        return IDLE                         # 猫咪静止或睡眠，不打扰

    if activity >= 20:
        return HUNTING                      # 猫咪活跃，启动逗猫

    return state                            # 维持当前状态
```

---

## 三、各模块详细伪代码

### 3.1 视觉追踪模块（perception/cat_tracker.py）

```python
# ═══════════════════════════════════════════════════════
# 功能：实时检测猫咪位置 + 驱动底盘跟随 + 输出活跃度评分
# ═══════════════════════════════════════════════════════

CONF_THRESHOLD  = 0.45   # YOLO 置信度下限
MIN_CAT_RATIO   = 0.05   # 猫咪占画面最小比例（过滤远距离）
TARGET_DISTANCE = 0.5    # 底盘与猫咪目标距离（归一化，0=左, 1=右）
FLOW_WINDOW     = 10     # 光流计算时间窗口（帧数）

class CatTracker:

    def __init__(self, api, model):
        self.api        = api
        self.model      = model
        self.frame_buf  = []            # 存储最近 FLOW_WINDOW 帧
        self.pid        = PIDController(Kp=0.8, Ki=0.0, Kd=0.2)

    # ── 检测猫咪 ────────────────────────────────────────
    function detect(frame) -> BoundingBox | None:

        results = model.predict(frame, classes=[15], conf=CONF_THRESHOLD)
        # COCO class 15 = cat

        best_box  = None
        best_conf = 0.0
        img_area  = frame.width * frame.height

        for box in results.boxes:
            area_ratio = box.area / img_area
            if area_ratio >= MIN_CAT_RATIO and box.conf > best_conf:
                best_conf = box.conf
                best_box  = box

        return best_box

    # ── 底盘跟随 ────────────────────────────────────────
    function follow(bbox):
        # 计算猫咪质心在画面中的横向偏移（-1 左 ~ +1 右）
        cx     = (bbox.x1 + bbox.x2) / 2
        offset = (cx / frame.width) - 0.5   # 归一化到 [-0.5, +0.5]

        correction = pid.compute(setpoint=0.0, current=offset)
        # correction > 0 → 猫在右侧 → 向右旋转
        # correction < 0 → 猫在左侧 → 向左旋转

        if abs(correction) > 0.1:
            if correction > 0:
                api.rotate(clockwise=True)
            else:
                api.rotate(clockwise=False)
        else:
            api.stop()

        # 前后距离控制：根据 bbox 面积估算距离
        bbox_ratio = bbox.area / (frame.width * frame.height)
        if bbox_ratio < 0.10:               # 猫咪太远
            api.move("forward")
        elif bbox_ratio > 0.40:             # 猫咪太近
            api.move("backward")
        else:
            api.cmd("stop")

    # ── 活跃度评分 ──────────────────────────────────────
    function compute_activity(frame) -> int:   # 返回 0-100

        self.frame_buf.append(frame)
        if len(self.frame_buf) < 2:
            return 0

        # Lucas-Kanade 稀疏光流
        prev  = to_grayscale(self.frame_buf[-2])
        curr  = to_grayscale(self.frame_buf[-1])
        flow  = lucas_kanade_optical_flow(prev, curr)

        # 计算平均运动向量幅度
        magnitudes    = [sqrt(dx²+dy²) for dx, dy in flow]
        avg_magnitude = mean(magnitudes)

        # 时间段加权（猫咪天然活跃期加权 1.3x）
        hour          = current_hour()
        time_weight   = 1.3 if hour in [5,6,7,17,18,19] else 1.0

        score = min(100, int(avg_magnitude * 10 * time_weight))

        if len(self.frame_buf) > FLOW_WINDOW:
            self.frame_buf.pop(0)           # 滑动窗口

        return score
```

---

### 3.2 叫声识别模块（perception/sound_classifier.py）

```python
# ═══════════════════════════════════════════════════════
# 功能：持续监听麦克风，分类猫咪情绪，写入共享变量
# ═══════════════════════════════════════════════════════

LABELS       = ["hungry", "affectionate", "alert", "satisfied"]
SILENCE_DB   = -40      # 低于此分贝视为静默，跳过推理
SAMPLE_RATE  = 16000
CHUNK_MS     = 500      # 每次推理音频片段长度

class SoundClassifier:

    def __init__(self, model_path):
        self.model        = load_cnn(model_path)
        self.latest_label = "satisfied"    # 共享变量，主循环读取
        self.latest_conf  = 0.0

    # ── 后台线程主函数 ──────────────────────────────────
    function run_loop():

        mic_stream = open_microphone(sample_rate=SAMPLE_RATE)

        while True:
            chunk = mic_stream.read(CHUNK_MS * SAMPLE_RATE // 1000)

            # 静默检测，避免对背景噪音推理
            if rms_db(chunk) < SILENCE_DB:
                continue

            # 特征提取
            mfcc    = extract_mfcc(chunk, n_mfcc=40, sample_rate=SAMPLE_RATE)
            # mfcc.shape = (40, time_frames)

            # 模型推理
            logits  = model.predict(mfcc)
            probs   = softmax(logits)
            label   = LABELS[argmax(probs)]
            conf    = max(probs)

            # 仅在置信度足够时更新
            if conf >= 0.70:
                self.latest_label = label
                self.latest_conf  = conf
                log(f"叫声识别: {label} ({conf:.0%})")

            # 分类响应
            if label == "hungry"       and conf >= 0.70:
                notify_owner("猫咪可能饿了，请检查食物")

            elif label == "alert"      and conf >= 0.70:
                # 紧急：写入全局 flag，主循环立即响应
                set_global_flag("ALERT", True)

            elif label == "affectionate":
                # 撒娇：触发温和互动模式（非紧急，由主循环在下个 tick 读取）
                pass
```

---

### 3.3 惊喜熵引擎（hunt/motion_generator.py）

```python
# ═══════════════════════════════════════════════════════
# 功能：生成让猫咪始终保持 70% 确定 + 30% 意外的动作序列
# 核心指标：惊喜熵（Surprise Entropy）
# ═══════════════════════════════════════════════════════

TARGET_CERTAINTY  = 0.70    # 猫咪预测成功率目标上限
PATTERN_DECAY     = 0.85    # 同一动作重复时置信度衰减系数
ACTION_POOL = [
    "sweep_left",           # 逗猫棒向左扫
    "sweep_right",          # 逗猫棒向右扫
    "jab_forward",          # 逗猫棒向前戳
    "retreat_slow",         # 缓慢后撤（模拟受伤猎物）
    "freeze",               # 突然静止（制造紧张感）
    "dash_away",            # 快速逃离
    "circle_clockwise",     # 顺时针绕圈
    "circle_ccw",           # 逆时针绕圈
]

class SurpriseEntropyEngine:

    def __init__(self, cat_profile):
        # 从个性档案初始化动作权重（历史偏好）
        self.weights      = cat_profile.action_weights or uniform_weights(ACTION_POOL)
        self.history      = []         # 最近 N 次动作记录
        self.success_rate = 0.5        # 猫咪扑中率滑动平均

    # ── 生成下一个动作 ──────────────────────────────────
    function next_action() -> str:

        # 1. 计算当前惊喜熵（Shannon 熵）
        entropy = compute_entropy(self.weights)
        # entropy 高 → 动作均匀分布 → 猫难预测（熵高）
        # entropy 低 → 某动作主导  → 猫容易预测（熵低）

        # 2. 如果猫咪扑中率 > TARGET_CERTAINTY，说明它已摸清规律
        #    → 强制降低最近常用动作的权重（打破规律）
        if self.success_rate > TARGET_CERTAINTY:
            for action in recent_top_actions(self.history, n=3):
                self.weights[action] *= PATTERN_DECAY
            normalize(self.weights)
            log("惊喜熵调节：猫咪预测率过高，已主动打破规律")

        # 3. 按权重采样（含 ε-greedy 探索：10% 概率随机选）
        if random() < 0.10:
            action = random_choice(ACTION_POOL)         # 探索
        else:
            action = weighted_sample(ACTION_POOL, self.weights)  # 利用

        # 4. 故意让猫咪偶尔"成功"（维持成就感）
        #    每 3 次动作中约 1 次使用慢速/可被扑中的动作
        if len(self.history) % 3 == 2:
            action = "retreat_slow"                     # 模拟受伤猎物

        self.history.append(action)
        return action

    # ── 更新反馈（猫咪是否扑中）──────────────────────────
    function update(action, cat_caught: bool):

        # 滑动平均更新扑中率
        self.success_rate = 0.9 * self.success_rate + 0.1 * int(cat_caught)

        # 正向强化：猫咪喜欢参与的动作 → 适当提权
        if cat_caught:
            self.weights[action] *= 1.1
        else:
            self.weights[action] *= 0.98   # 失败略降权，防止过度重复

        normalize(self.weights)


# ── 逗猫会话主流程 ──────────────────────────────────────
function run_hunt_session(api, yolo, engine, frame, cat_bbox) -> SessionResult:

    session    = SessionResult()
    start_time = now()
    wand_mode  = True           # 初始使用逗猫棒；False = 激光模式

    while True:

        # 1. 跟随猫咪
        frame   = api.snapshot()
        bbox    = detect_cat(yolo, frame)
        if bbox:
            tracker.follow(bbox)

        # 2. 生成并执行动作
        action = engine.next_action()
        execute_arm_action(api, action)     # 向机械臂发送指令

        # 3. 检测猫咪是否扑中
        cat_caught = detect_catch_event(api, frame, bbox)
        engine.update(action, cat_caught)

        if cat_caught:
            api.dispense_treat()            # 投食（零食闭环）
            session.add_event("catch", frame)

        # 4. 萌度评分 → 高光帧捕捉
        score = cuteness_score(frame, bbox)
        if score > 0.7:
            session.highlights.append(frame)

        # 5. 每 5 分钟切换逗猫模式（保持新鲜感）
        if elapsed(start_time) % 300 == 0:
            wand_mode = not wand_mode
            if wand_mode:
                switch_to_wand(api)
            else:
                switch_to_laser(api)

        # 6. 会话结束判定
        activity = tracker.compute_activity(frame)
        if activity < 15:                   # 猫咪失去兴趣
            break
        if bbox is None and elapsed(start_time) > 30:  # 猫咪离开超 30s
            break
        if elapsed(start_time) > 900:       # 最长 15 分钟
            break

    api.stop()
    session.duration = elapsed(start_time)
    return session
```

---

### 3.4 饮水监测模块（care/health_monitor.py）

```python
# ═══════════════════════════════════════════════════════
# 功能：7×24h 监测猫咪饮水，异常时推送预警
# ═══════════════════════════════════════════════════════

SAMPLE_INTERVAL  = 30       # 传感器采样间隔（秒）
ALERT_THRESHOLD  = 12 * 3600  # 12 小时无饮水触发预警

class HealthMonitor:

    def __init__(self, api, cat_weight_kg):
        self.api             = api
        self.daily_recommend = cat_weight_kg * 50   # ml，每公斤体重50ml
        self.last_drink_at   = now()
        self.today_total_ml  = 0
        self.bowl_area_cm2   = 50.0    # 水碗横截面积（需标定）

    # ── 后台监测线程 ────────────────────────────────────
    function run_loop():

        prev_level = read_water_level()     # 初始液位（mm）

        while True:
            sleep(SAMPLE_INTERVAL)

            curr_level = read_water_level()
            drop_mm    = prev_level - curr_level

            if drop_mm > 1.0:               # 液位下降 > 1mm 视为有效饮水
                volume_ml = drop_mm * self.bowl_area_cm2 / 10   # mm³ → ml
                self.today_total_ml += volume_ml
                self.last_drink_at   = now()
                log(f"检测到饮水 {volume_ml:.1f} ml，今日累计 {self.today_total_ml:.0f} ml")

            elif drop_mm < -2.0:            # 液位上升，可能是加水
                prev_level = curr_level     # 重置基准，不计入饮水

            prev_level = curr_level

            # 异常判定：超过阈值时间未饮水
            idle_seconds = now() - self.last_drink_at
            if idle_seconds > ALERT_THRESHOLD:
                notify_owner(
                    f"⚠️ 猫咪已 {idle_seconds//3600:.0f} 小时未饮水，请检查"
                )
                # 机器人主动驶向猫咪发出轻声提示
                api.move_to_cat()
                api.play_cat_sound(1)

            # 每日零点重置今日饮水量
            if is_midnight():
                save_daily_water(self.today_total_ml, self.daily_recommend)
                self.today_total_ml = 0
```

---

### 3.5 表情包生成模块（report/meme_generator.py）

```python
# ═══════════════════════════════════════════════════════
# 功能：检测高光帧 → VLM 配文 → 合成表情包 → 推送
# ═══════════════════════════════════════════════════════

RING_BUFFER_SIZE = 60       # 环形缓冲帧数（2秒 @ 30fps）
CUTENESS_THRESHOLD = 0.70   # 萌度评分触发阈值
DAILY_MEME_LIMIT   = 20     # 每日表情包上限

class MemeGenerator:

    def __init__(self, api, yolo, vlm_client):
        self.api         = api
        self.yolo        = yolo
        self.vlm         = vlm_client          # VLM：LLaVA / GPT-4o Vision
        self.ring_buf    = RingBuffer(RING_BUFFER_SIZE)
        self.today_count = 0

    # ── 主循环（与主程序并行）──────────────────────────
    function run_loop():

        while True:
            frame = api.snapshot()
            ring_buf.push(frame)

            bbox    = detect_cat(yolo, frame)
            if bbox is None:
                sleep(0.1)
                continue

            score   = cuteness_score(frame, bbox)

            if score >= CUTENESS_THRESHOLD and today_count < DAILY_MEME_LIMIT:
                best_frame = pick_best_frame(ring_buf, yolo)
                meme       = generate_meme(best_frame, bbox)
                save_and_push(meme)
                today_count += 1

            sleep(0.1)

    # ── 萌度评分 ────────────────────────────────────────
    function cuteness_score(frame, bbox) -> float:    # 返回 0.0-1.0

        cropped     = crop_with_padding(frame, bbox)

        # 三个子指标
        face_conf   = detect_frontal_face(cropped)   # 正脸置信度
        eye_ratio   = eye_area / face_area            # 双眼占脸部比例
        mouth_open  = detect_mouth_open(cropped)      # 嘴部开合度（0-1）

        score = 0.5 * face_conf + 0.3 * eye_ratio + 0.2 * mouth_open
        return clamp(score, 0.0, 1.0)

    # ── 从环形缓冲选最佳帧 ─────────────────────────────
    function pick_best_frame(ring_buf, yolo) -> Frame:

        candidates  = ring_buf.get_all()
        best_frame  = None
        best_score  = 0.0

        for frame in candidates:
            bbox  = detect_cat(yolo, frame)
            if bbox:
                s = cuteness_score(frame, bbox)
                if s > best_score:
                    best_score = s
                    best_frame = frame

        return best_frame or candidates[-1]  # fallback: 最新帧

    # ── 生成表情包 ─────────────────────────────────────
    function generate_meme(frame, bbox) -> Image:

        cropped = crop_with_padding(frame, bbox, pad=40)

        # VLM 生成配文（3 种风格）
        prompt   = "这是一张猫咪的照片。请用以下三种风格各写一句简短的表情包配文（中文，10字内）：1.严肃体 2.自恋体 3.茫然体"
        response = vlm.complete(image=cropped, text=prompt)
        captions = parse_three_captions(response)       # 解析三条配文

        # 随机选一种风格
        top, bottom = random.choice(captions).split("——")

        # Pillow 合成：黑描边 + 白字，经典表情包风格
        img = to_pil(cropped)
        img = draw_meme_text(img, top=top, bottom=bottom)
        return img
```

---

### 3.6 每日日报模块（report/daily_diary.py）

```python
# ═══════════════════════════════════════════════════════
# 功能：每日 20:00 汇总数据 → LLM 生成自然语言日记 → 推送
# ═══════════════════════════════════════════════════════

REPORT_HOUR = 20    # 每日推送时间

class DailyDiary:

    def __init__(self, db, llm_client, push_client):
        self.db     = db            # SQLite 本地数据库
        self.llm    = llm_client    # LLM API（Claude / GPT）
        self.push   = push_client   # Firebase Cloud Messaging

    # ── 定时触发 ────────────────────────────────────────
    function run_loop():

        while True:
            if current_hour() == REPORT_HOUR and not reported_today():
                diary = generate_diary()
                push.send(title="今日猫咪日报 🐱", body=diary)
                mark_reported_today()
            sleep(60)   # 每分钟检查一次

    # ── 聚合今日数据 ────────────────────────────────────
    function aggregate_today() -> DayData:

        data = db.query("""
            SELECT
                SUM(activity_score)   AS total_activity,
                SUM(session_minutes)  AS play_minutes,
                COUNT(catch_events)   AS catches,
                AVG(emotion_dist)     AS mood_json,
                SUM(water_ml)         AS water_intake,
                MAX(cuteness_score)   AS best_moment_score
            FROM events
            WHERE date = today()
        """)

        top_memes = db.query(
            "SELECT path FROM memes WHERE date=today() ORDER BY score DESC LIMIT 3"
        )

        return DayData(data, top_memes)

    # ── LLM 生成日记 ────────────────────────────────────
    function generate_diary() -> str:

        day   = aggregate_today()

        # 构建结构化 prompt，让 LLM 以猫咪口吻写日记
        prompt = f"""
你是一只住在上海的猫咪，请用第一人称写今天的日记（200字以内，语气慵懒可爱）。

今日数据：
- 活跃总量：{day.total_activity} 分（满分 100/小时）
- 玩耍时长：{day.play_minutes} 分钟
- 成功扑到 Hunter：{day.catches} 次
- 今日心情分布：{day.mood_json}
- 饮水量：{day.water_intake} ml（推荐 {daily_recommend} ml）
- 最精彩瞬间萌度评分：{day.best_moment_score:.0%}

请写日记，结尾用一句话描述对明天的期待。
        """

        diary = llm.complete(prompt, max_tokens=300)
        return diary
```

---

### 3.7 记忆盒子模块（memory/memory_box.py）

```python
# ═══════════════════════════════════════════════════════
# 功能：长期记录猫咪互动历史，构建并更新个性档案
# 护城河：时间越长，模型越精准，用户越无法换设备
# ═══════════════════════════════════════════════════════

class MemoryBox:

    def __init__(self, db_path):
        self.db = SQLiteDB(db_path)
        self.db.init_schema("""
            CREATE TABLE IF NOT EXISTS sessions (
                id              INTEGER PRIMARY KEY,
                timestamp       DATETIME,
                duration_sec    INT,
                catches         INT,
                avg_activity    FLOAT,
                dominant_emotion TEXT,
                actions_json    TEXT      -- 动作序列及权重快照
            );
            CREATE TABLE IF NOT EXISTS profile (
                key     TEXT PRIMARY KEY,
                value   TEXT             -- JSON 序列化
            );
        """)

    # ── 会话结束后调用：保存 + 更新档案 ───────────────
    function update(session: SessionResult):

        # 1. 持久化本次会话
        db.insert("sessions", {
            "timestamp":       now(),
            "duration_sec":    session.duration,
            "catches":         session.catch_count,
            "avg_activity":    session.avg_activity,
            "dominant_emotion":session.dominant_emotion,
            "actions_json":    serialize(session.action_log),
        })

        # 2. 增量更新个性档案（Bandit 模型）
        profile = load_profile()

        for action, count, success in session.action_stats:
            # 贝叶斯更新：更新每个动作的 Beta 分布参数
            profile.alpha[action] += success
            profile.beta[action]  += (count - success)

        # 3. 更新作息规律（高斯分布，记录活跃时间段）
        hour = session.start_hour
        profile.activity_hours[hour] = (
            0.9 * profile.activity_hours[hour] +
            0.1 * session.avg_activity
        )

        save_profile(profile)
        log(f"个性档案已更新，累计会话 {db.count('sessions')} 次")

    # ── 生成偏好洞察（每周推送）───────────────────────
    function generate_weekly_insight() -> str:

        sessions = db.query(
            "SELECT * FROM sessions WHERE timestamp > 7_days_ago ORDER BY timestamp"
        )

        # 统计本周最受欢迎动作
        action_freq  = count_actions(sessions)
        peak_hours   = find_peak_hours(sessions)
        catch_rate   = total_catches / total_attempts

        insight = f"""
本周互动洞察：
- 最爱动作：{top_action(action_freq)}（占 {top_action_pct:.0%}）
- 活跃高峰：每天 {peak_hours[0]}:00–{peak_hours[1]}:00
- 扑中率：{catch_rate:.0%}（{'高手' if catch_rate>0.5 else '还在练习'}）
- 累计互动：{sum(s.duration for s in sessions)//60} 分钟
        """
        return insight

    # ── 生命回忆录（猫咪离世后调用）──────────────────
    function compile_life_memoir() -> Document:

        all_sessions = db.query("SELECT * FROM sessions ORDER BY timestamp")
        top_memes    = db.query(
            "SELECT * FROM memes ORDER BY cuteness_score DESC LIMIT 50"
        )
        highlights   = db.query(
            "SELECT * FROM events WHERE type='catch' ORDER BY timestamp"
        )

        memoir = LLM.compile(
            prompt   = "根据以下互动记录，生成一份温情的猫咪一生回忆录（按年月组织）",
            data     = {sessions, highlights, top_memes},
            max_tokens = 2000,
        )

        export_pdf(memoir, top_memes, filename=f"{cat_name}_的一生.pdf")
        return memoir
```

---

## 四、模块间数据流总览

```
摄像头帧
  └─→ CatTracker.detect()      → bbox, activity_score
  └─→ MemeGenerator.ring_buf   → 高光帧候选

麦克风流
  └─→ SoundClassifier.run()    → emotion_label → 全局 flag

bbox + activity + emotion
  └─→ StateMachine.transition() → 当前状态

HUNTING 状态
  └─→ CatTracker.follow()       → 底盘 PID 跟随
  └─→ SurpriseEntropyEngine.next_action() → 动作指令
  └─→ api.execute_arm_action()  → 机械臂执行
  └─→ detect_catch_event()      → 扑中 → api.dispense_treat()
  └─→ SurpriseEntropyEngine.update() → 权重更新

会话结束
  └─→ MemoryBox.update()        → SQLite 持久化 + 档案更新
  └─→ MemeGenerator.generate()  → 表情包 → APP 推送

每日定时
  └─→ DailyDiary.generate()     → LLM 日记 → FCM 推送
  └─→ HealthMonitor.check()     → 饮水异常 → 预警推送
```

---

## 五、关键算法参数速查

| 参数 | 值 | 说明 |
|------|----|------|
| `TARGET_CERTAINTY` | 0.70 | 惊喜熵目标：猫咪预测成功率上限 |
| `PATTERN_DECAY` | 0.85 | 打破规律时的权重衰减系数 |
| `epsilon` | 0.10 | ε-greedy 探索概率 |
| `CONF_THRESHOLD` | 0.45 | YOLO 置信度下限 |
| `MIN_CAT_RATIO` | 0.05 | 猫咪占画面最小面积比 |
| `CUTENESS_THRESHOLD` | 0.70 | 表情包触发萌度阈值 |
| `ALERT_THRESHOLD` | 12h | 饮水异常预警时长 |
| `PID Kp/Ki/Kd` | 0.8/0.0/0.2 | 底盘跟随 PID 参数（需现场标定） |
| `RING_BUFFER_SIZE` | 60帧 | 表情包环形缓冲（≈ 2秒） |
