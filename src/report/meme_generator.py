"""
表情包生成器
流程：摄像头快照 → YOLOv8 检测猫 → 裁剪 → 加文字 → 保存

依赖：
    pip install ultralytics pillow requests opencv-python-headless
字体（Pi 上）：
    sudo apt install fonts-noto-cjk
"""

import random
import time
from pathlib import Path
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from ultralytics import YOLO

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from api_client import HunterAPI

# ── 配置 ──────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).parent.parent.parent / "output" / "memes"
FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # fallback
]
POLL_INTERVAL = 8       # 每 N 秒抓一帧
COOLDOWN = 60           # 两张表情包之间的最短间隔（秒）
MIN_CONF = 0.45         # YOLO 最低置信度
MIN_CAT_RATIO = 0.05    # 猫占画面面积的最小比例（过滤太远/太小的猫）

# ── 文案库 ───────────────────────────────────────────────
MEME_PAIRS = [
    ("主人不在的第 1 天", "开始制定计划"),
    ("发现可疑羽毛", "已列为重点盯梢对象"),
    ("刚刚扑空了", "这是战术后撤"),
    ("完美的一天", "从睡到睡"),
    ("今天活跃度：92", "建议奖励零食"),
    ("表面在睡觉", "实际在侦察"),
    ("Hunter 以为我没发现它", "我只是给它面子"),
    ("扑中了！", "假装这是我的计划"),
    ("又到饭点了", "开始表演饥饿"),
    ("刚刚巡逻了 27 圈", "安全，暂时"),
]


# ── 工具函数 ─────────────────────────────────────────────
def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def draw_meme_text(img: Image.Image, top: str, bottom: str) -> Image.Image:
    draw = ImageDraw.Draw(img)
    w, h = img.size
    font = load_font(size=max(24, int(h * 0.08)))

    def put_text(text: str, y: int, anchor: str = "mt"):
        # 黑色描边，白色正文，经典表情包风格
        for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2), (-2, 2), (2, -2)]:
            draw.text((w // 2 + dx, y + dy), text, font=font, fill="black", anchor=anchor)
        draw.text((w // 2, y), text, font=font, fill="white", anchor=anchor)

    put_text(top, int(h * 0.04), anchor="mt")
    put_text(bottom, int(h * 0.96), anchor="mb")
    return img


def frame_to_pil(frame: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def crop_with_padding(frame: np.ndarray, box: tuple, pad: int = 40) -> np.ndarray:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    x1 = max(0, x1 - pad)
    y1 = max(0, y1 - pad)
    x2 = min(w, x2 + pad)
    y2 = min(h, y2 + pad)
    return frame[y1:y2, x1:x2]


def detect_best_cat(model: YOLO, frame: np.ndarray):
    """
    返回置信度最高的猫的 (x1,y1,x2,y2)，不符合条件返回 None。
    COCO class 15 = cat
    """
    results = model(frame, classes=[15], conf=MIN_CONF, verbose=False)
    boxes = results[0].boxes
    if len(boxes) == 0:
        return None

    img_area = frame.shape[0] * frame.shape[1]
    best_box = None
    best_conf = 0.0

    for box in boxes:
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        area_ratio = (x2 - x1) * (y2 - y1) / img_area
        conf = float(box.conf[0])
        if area_ratio >= MIN_CAT_RATIO and conf > best_conf:
            best_conf = conf
            best_box = (x1, y1, x2, y2)

    return best_box


def save_meme(img: Image.Image) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"meme_{ts}.jpg"
    img.save(path, quality=92)
    return path


# ── 主循环 ───────────────────────────────────────────────
def run(api: HunterAPI, model: YOLO):
    last_meme_at = 0.0
    print(f"Meme generator running. Output → {OUTPUT_DIR}")

    while True:
        try:
            frame = api.snapshot()
            box = detect_best_cat(model, frame)

            now = time.time()
            if box and (now - last_meme_at) >= COOLDOWN:
                cropped = crop_with_padding(frame, box)
                img = frame_to_pil(cropped)
                top, bottom = random.choice(MEME_PAIRS)
                img = draw_meme_text(img, top, bottom)
                path = save_meme(img)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Meme saved → {path.name}")
                last_meme_at = now
            else:
                status = "cat found, cooling down" if box else "no cat"
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {status}")

        except KeyboardInterrupt:
            print("Stopped.")
            break
        except Exception as e:
            print(f"Error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    api = HunterAPI()
    model = YOLO("yolov8n.pt")  # 首次运行自动下载 ~6MB
    run(api, model)
