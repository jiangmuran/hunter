"""
表情包生成器 v3
核心改进（对比 v2）：
  1. MJPEG 流替代 snapshot 连拍：3s 窗口 ~36 帧，全局清晰度预筛后
     仅对 top-8 帧跑 YOLO，帧池扩大 7× 且推理量不变
  2. 头部清晰度替代全身清晰度：bbox 上 40% 为猫脸区，猫脸糊的帧
     直接降权，再也不会把闭眼糊脸选成神图
  3. 动作峰值帧检测：追踪流内各帧 bbox 位移，优先"刚停下来"的
     那一帧（运动最激烈之后 → 猫定格做鬼脸的黄金瞬间）
  4. 超声波距离窗口：api.state() 读 S1/S2/S3，250-900mm 为优质
     拍摄区；三传感器均无障碍时允许降级（猫在旁侧不挡正前）
  5. CLAHE 自动曝光补偿：LAB 空间对亮度通道做限制对比增强，
     暗光 / 过曝场景下细节找回来
  6. portrait 姿态新分类：正脸居中 + 头部高清 → 命中率最高的
     神图姿态，独立文案池
  7. 文案按距离分档细化：超声波确认猫近（< 450mm）时启用
     "近景专属"文案强化冲击感
"""

import random
import time
from pathlib import Path
from datetime import datetime

import cv2
import numpy as np
import requests as _requests   # 仅用于读 MJPEG 流，不修改 api_client
from PIL import Image, ImageDraw, ImageFont
from ultralytics import YOLO

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from software.api_client import HunterAPI

# ── 配置 ──────────────────────────────────────────────────────────────────
OUTPUT_DIR      = Path(__file__).parent.parent.parent / "output" / "memes"
FONT_PATHS      = [
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

POLL_INTERVAL   = 6        # 主循环探针间隔（秒）
COOLDOWN        = 60       # 两张表情包最短间隔（秒）
MIN_CONF        = 0.45     # YOLO 置信度门槛
MIN_CAT_RATIO   = 0.05     # 猫最小面积比

STREAM_WINDOW   = 3.0      # MJPEG 流采帧窗口（秒）
STREAM_TOP_K    = 8        # 预筛后送 YOLO 的帧数

HEAD_REGION     = 0.40     # bbox 上多少比例算头部
MIN_HEAD_SHARP  = 80.0     # 头部 Laplacian 门槛（低于此 → moving）

DIST_MIN_MM     = 250      # 超声波最近距离（mm）
DIST_MAX_MM     = 900      # 超声波最远距离（mm）
DIST_CLOSE_MM   = 450      # 小于此距离启用"近景文案"

CROP_RATIO      = 4 / 3    # 输出裁剪宽高比

# ── 文案库（6 姿态 + 近景变体 + 兜底）────────────────────────────────────
MEME_BANK: dict[str, list[tuple[str, str]]] = {
    # 正脸居中、头部高清 ── 神图概率最高
    "portrait": [
        ("我知道你在看我", "我也在看你"),
        ("审判时刻", "你知道你做了什么"),
        ("这表情", "是天生的"),
        ("检测到摄像头", "已开始表演"),
        ("对视三秒", "我赢了"),
        ("别跑", "坐下来聊聊"),
    ],
    # 紧贴镜头，面积 ≥ 30%
    "closeup": [
        ("又到饭点了", "开始表演饥饿"),
        ("画面太冲击", "建议折叠"),
        ("镜头推近一点", "再近一点"),
        ("Hunter 近在眼前", "下次不会跑掉的"),
        ("这已经不是脸了", "这是整个世界"),
    ],
    # 横躺，宽高比 > 1.8
    "lying": [
        ("完美的一天", "从睡到睡"),
        ("今日计划", "已 100% 完成"),
        ("战略性摆烂", "第 47 天"),
        ("这不叫懒", "这叫资源优化"),
        ("工作与生活的平衡", "我选择生活"),
        ("重力让我躺下", "我只是顺应自然"),
    ],
    # 端坐 / 站立，宽高比 < 0.72
    "sitting": [
        ("主人不在的第 1 天", "开始制定计划"),
        ("王者归来", "你们都要听我的"),
        ("发现可疑活动", "已列为重点盯梢对象"),
        ("我坐在这里", "就是最大的威胁"),
        ("评估中", "结果稍后公布"),
    ],
    # 缩成面包状，宽高比 0.72–1.8
    "loaf": [
        ("表面在充电", "实际在侦察"),
        ("当前状态：面包", "随时可弹出"),
        ("能量蓄积中", "请勿打扰"),
        ("我没有手", "但我有计划"),
        ("主人以为我在休息", "我只是在等时机"),
    ],
    # 运动模糊 / 高速移动
    "moving": [
        ("这是战术后撤", "不是跑路"),
        ("刚刚扑空了", "属于战略调整"),
        ("速度太快拍不清楚", "你懂的"),
        ("此处有猫经过", "全速不减"),
        ("我没在跑", "我在飞"),
    ],
}
# 超声波确认近景时的额外文案，叠加到已选姿态文案池
CLOSE_RANGE_BONUS: list[tuple[str, str]] = [
    ("你好，我好，大家好", "但你站远点"),
    ("进入攻击范围", "请保持镇定"),
    ("已锁定目标", "准备发动魅力攻势"),
    ("这么近", "是来送饭的吗"),
]
FALLBACK_PAIRS = [
    ("刚刚巡逻了 27 圈", "安全，暂时"),
    ("扑中了！", "假装这是我的计划"),
    ("今天活跃度：92", "建议奖励零食"),
    ("发现可疑羽毛", "已列为重点盯梢对象"),
]


# ── 图像工具 ──────────────────────────────────────────────────────────────

def load_font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def draw_meme_text(img: Image.Image, top: str, bottom: str) -> Image.Image:
    draw = ImageDraw.Draw(img)
    w, h = img.size
    font = load_font(max(24, int(h * 0.08)))

    def put(text: str, y: int, anchor: str) -> None:
        offsets = [(-2,0),(2,0),(0,-2),(0,2),(-2,-2),(2,2),(-2,2),(2,-2)]
        for dx, dy in offsets:
            draw.text((w//2+dx, y+dy), text, font=font, fill="black", anchor=anchor)
        draw.text((w//2, y), text, font=font, fill="white", anchor=anchor)

    put(top,    int(h * 0.04), "mt")
    put(bottom, int(h * 0.96), "mb")
    return img


def frame_to_pil(frame: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def clahe_enhance(frame: np.ndarray) -> np.ndarray:
    """CLAHE 对 LAB 亮度通道做限制对比增强，修复暗光 / 过曝，不影响色调。"""
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def head_sharpness(frame: np.ndarray, box: tuple) -> float:
    """bbox 上 HEAD_REGION 比例（猫脸区）的 Laplacian 方差。"""
    x1, y1, x2, y2 = box
    head_bot = y1 + max(1, int((y2 - y1) * HEAD_REGION))
    roi = frame[y1:head_bot, x1:max(x1+1, x2)]
    if roi.size == 0:
        return 0.0
    return float(cv2.Laplacian(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def score_frame(frame: np.ndarray, box: tuple, motion_bonus: float = 0.0) -> float:
    """
    综合评分（0–1）：
        头部清晰度  42%  —— 猫脸清晰才有神图
        大小        28%  —— 近景大猫更有张力
        构图居中    20%  —— 猫在画面中央
        动作奖励    10%  —— 刚经历大动作后的定格帧
    """
    fh, fw = frame.shape[:2]
    x1, y1, x2, y2 = box
    sharp_s  = min(head_sharpness(frame, box) / 400.0, 1.0)
    size_s   = min((x2-x1)*(y2-y1) / (fh*fw) / 0.35, 1.0)
    cx_norm  = (x1+x2) / 2 / fw
    cy_norm  = (y1+y2) / 2 / fh
    center_s = 1.0 - (abs(cx_norm - 0.5) + abs(cy_norm - 0.5))
    return sharp_s*0.42 + size_s*0.28 + center_s*0.20 + motion_bonus*0.10


def smart_crop(frame: np.ndarray, box: tuple, ratio: float = CROP_RATIO) -> np.ndarray:
    """以猫为中心裁剪，保持 ratio（宽/高），留 50% 边距，边界安全 clamp。"""
    fh, fw = frame.shape[:2]
    x1, y1, x2, y2 = box
    cx = (x1+x2) // 2
    cy = (y1+y2) // 2
    bw, bh = x2-x1, y2-y1

    cw = int(bw * 1.5)
    ch = int(bh * 1.5)
    if cw / max(ch, 1) < ratio:
        cw = int(ch * ratio)
    else:
        ch = int(cw / ratio)

    left  = max(0, cx - cw//2)
    top   = max(0, cy - ch//2)
    right = min(fw, left + cw)
    bot   = min(fh, top + ch)
    left  = max(0, right - cw)
    top   = max(0, bot - ch)
    return frame[top:bot, left:right]


# ── YOLO 检测 ─────────────────────────────────────────────────────────────

def detect_best_cat(model: YOLO, frame: np.ndarray) -> tuple | None:
    """
    conf × √area_ratio 综合分最高的猫 bbox。
    √ 比线性更平滑地惩罚远猫，近景猫被强力优选。
    """
    results = model(frame, classes=[15], conf=MIN_CONF, verbose=False)
    boxes = results[0].boxes
    if not len(boxes):
        return None

    img_area = frame.shape[0] * frame.shape[1]
    best_box, best_score = None, 0.0
    for b in boxes:
        x1, y1, x2, y2 = map(int, b.xyxy[0])
        ar = (x2-x1)*(y2-y1) / img_area
        if ar < MIN_CAT_RATIO:
            continue
        s = float(b.conf[0]) * (ar ** 0.5)
        if s > best_score:
            best_score, best_box = s, (x1, y1, x2, y2)
    return best_box


# ── 姿态分类 ──────────────────────────────────────────────────────────────

def classify_pose(frame: np.ndarray, box: tuple, is_sharp: bool) -> str:
    """
    7 档姿态（优先级从高到低）：
        moving   → 头部模糊（高速运动）
        portrait → 清晰 + 居中 + 适中尺寸 + 头部高清（对镜正脸，神图首选）
        closeup  → 面积 ≥ 30%（大特写）
        lying    → 宽高比 > 1.8（横躺）
        sitting  → 宽高比 < 0.72（端坐）
        loaf     → 其余（面包状）
    """
    if not is_sharp:
        return "moving"

    x1, y1, x2, y2 = box
    bw, bh = x2-x1, y2-y1
    fh, fw = frame.shape[:2]
    area   = (bw * bh) / (fh * fw)
    cx_n   = (x1+x2) / 2 / fw

    # portrait：正脸居中 + 头部更清晰
    if (0.28 <= cx_n <= 0.72
            and 0.08 <= area < 0.30
            and head_sharpness(frame, box) >= MIN_HEAD_SHARP * 1.3):
        return "portrait"

    if area >= 0.30:
        return "closeup"

    ratio = bw / max(bh, 1)
    if ratio > 1.8:
        return "lying"
    if ratio < 0.72:
        return "sitting"
    return "loaf"


# ── MJPEG 流采帧 ──────────────────────────────────────────────────────────

def _read_stream_frames(api: HunterAPI, window_s: float) -> list[np.ndarray]:
    """
    读取 MJPEG 流 window_s 秒，扫描 JPEG magic bytes（FFD8/FFD9）提取帧。
    不依赖 multipart boundary，对各种 Pi 摄像头固件都兼容。
    """
    frames: list[np.ndarray] = []
    buf   = b""
    deadline = time.time() + window_s

    try:
        resp = _requests.get(
            api.stream_url(),
            stream=True,
            timeout=(3.0, window_s + 2.0),
        )
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=8192):
            if time.time() > deadline:
                break
            buf += chunk
            while True:
                soi = buf.find(b"\xff\xd8")
                if soi == -1:
                    buf = b""
                    break
                eoi = buf.find(b"\xff\xd9", soi + 2)
                if eoi == -1:
                    buf = buf[soi:]
                    break
                f = cv2.imdecode(
                    np.frombuffer(buf[soi:eoi+2], np.uint8),
                    cv2.IMREAD_COLOR,
                )
                buf = buf[eoi+2:]
                if f is not None:
                    frames.append(f)
        resp.close()
    except Exception as e:
        print(f"[meme] stream error: {e}")
    return frames


def stream_and_pick(api: HunterAPI, model: YOLO) -> tuple:
    """
    主选帧流程：
        1. 读流 STREAM_WINDOW 秒（~36 帧 @12fps）
        2. 全局 Laplacian 预筛：取最清晰的 STREAM_TOP_K 帧（维持时间序）
        3. YOLO 推理 + 动作峰值评分
        4. 返回最高分帧 (frame, box, is_sharp)
    """
    raw = _read_stream_frames(api, STREAM_WINDOW)
    if not raw:
        return None, None, False

    # 预筛：全帧 Laplacian（比 YOLO 快 ~100×）
    def global_lap(f: np.ndarray) -> float:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(g, cv2.CV_64F).var())

    ranked   = sorted(range(len(raw)), key=lambda i: global_lap(raw[i]), reverse=True)
    top_idxs = sorted(ranked[:STREAM_TOP_K])   # 恢复时间顺序

    # YOLO 推理，记录时序结果用于动作评分
    timed: list[tuple[int, np.ndarray, tuple]] = []   # (original_idx, frame, box)
    for i in top_idxs:
        box = detect_best_cat(model, raw[i])
        if box:
            timed.append((i, raw[i], box))

    if not timed:
        return None, None, False

    # 动作峰值：计算相邻检测帧的 bbox 中心位移，归一化为 [0,1]
    # 位移越大说明前一帧猫在高速运动，当前帧是"刚停下"的定格
    centroids = [((b[0]+b[2])/2, (b[1]+b[3])/2) for _, _, b in timed]
    displacements = [0.0]
    for k in range(1, len(centroids)):
        dx = centroids[k][0] - centroids[k-1][0]
        dy = centroids[k][1] - centroids[k-1][1]
        displacements.append((dx**2 + dy**2) ** 0.5)
    max_disp = max(displacements) or 1.0
    motion_bonuses = [d / max_disp for d in displacements]

    # 最终评分
    candidates = [
        (score_frame(fr, bx, mb), fr, bx)
        for (_, fr, bx), mb in zip(timed, motion_bonuses)
    ]
    candidates.sort(key=lambda t: t[0], reverse=True)
    _, best_frame, best_box = candidates[0]
    is_sharp = head_sharpness(best_frame, best_box) >= MIN_HEAD_SHARP
    return best_frame, best_box, is_sharp


# ── 超声波辅助 ────────────────────────────────────────────────────────────

def get_ultrasonic(api: HunterAPI) -> dict:
    """
    读取三个超声波最新数据。失败时返回空 dict。
    返回格式：{"1": {"distance_mm": int, "has_obstacle": bool}, ...}
    """
    try:
        return api.state()["state"]["ultra"]
    except Exception:
        return {}


def check_distance(ultra: dict) -> tuple[bool, float | None]:
    """
    判断当前距离是否适合拍摄。
    优先用正前方 S1；S1 无数据时降级到 S2/S3 任意一个。
    返回 (in_window, dist_mm_or_None)。
    """
    for sid in ("1", "2", "3"):
        sensor = ultra.get(sid)
        if sensor and sensor.get("distance_mm"):
            d = float(sensor["distance_mm"])
            return DIST_MIN_MM <= d <= DIST_MAX_MM, d
    return True, None   # 无超声波数据时放行


# ── 主循环 ────────────────────────────────────────────────────────────────

def run(api: HunterAPI, model: YOLO) -> None:
    last_meme_at = 0.0
    print(f"Meme generator v3 running.  Output → {OUTPUT_DIR}")

    while True:
        try:
            now = time.time()

            # 1. 探针帧：快速确认有猫，避免无意义流读取
            try:
                probe = api.snapshot()
            except Exception as e:
                print(f"[{datetime.now():%H:%M:%S}] snapshot error: {e}")
                time.sleep(POLL_INTERVAL)
                continue

            if not detect_best_cat(model, probe):
                print(f"[{datetime.now():%H:%M:%S}] no cat")
                time.sleep(POLL_INTERVAL)
                continue

            if (now - last_meme_at) < COOLDOWN:
                print(
                    f"[{datetime.now():%H:%M:%S}] cat found — "
                    f"cooldown {int(COOLDOWN-(now-last_meme_at))}s"
                )
                time.sleep(POLL_INTERVAL)
                continue

            # 2. 超声波距离检查（失败时不阻断）
            ultra = get_ultrasonic(api)
            in_window, dist_mm = check_distance(ultra)
            if not in_window:
                print(
                    f"[{datetime.now():%H:%M:%S}] cat found but "
                    f"distance {dist_mm:.0f}mm out of [{DIST_MIN_MM},{DIST_MAX_MM}]mm"
                )
                time.sleep(POLL_INTERVAL)
                continue

            dist_str = f"{dist_mm:.0f}mm" if dist_mm else "dist=N/A"
            print(f"[{datetime.now():%H:%M:%S}] cat @ {dist_str} — reading stream…")

            # 3. 流式采帧 → 预筛 → YOLO → 动作评分 → 最佳帧
            frame, box, is_sharp = stream_and_pick(api, model)
            if frame is None:
                time.sleep(POLL_INTERVAL)
                continue

            # 4. 姿态 → 文案（近景时混入近景文案增加概率）
            pose = classify_pose(frame, box, is_sharp)
            pool = list(MEME_BANK.get(pose, FALLBACK_PAIRS))
            if dist_mm is not None and dist_mm < DIST_CLOSE_MM:
                pool = pool + CLOSE_RANGE_BONUS  # 近景额外选项
            top, bottom = random.choice(pool)

            # 5. 智能裁剪 → CLAHE 增强 → 上字
            crop = smart_crop(frame, box)
            crop = clahe_enhance(crop)
            img  = frame_to_pil(crop)
            img  = draw_meme_text(img, top, bottom)

            # 6. 保存
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = OUTPUT_DIR / f"meme_{ts}.jpg"
            img.save(path, quality=92)

            h_sharp = head_sharpness(frame, box)
            print(
                f"[{datetime.now():%H:%M:%S}] "
                f"pose={pose}  head_sharp={h_sharp:.0f}  {dist_str}  → {path.name}"
            )
            last_meme_at = time.time()

        except KeyboardInterrupt:
            print("Stopped.")
            break
        except Exception as e:
            print(f"[meme] error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    api   = HunterAPI()
    model = YOLO("yolo11n.pt")
    run(api, model)
