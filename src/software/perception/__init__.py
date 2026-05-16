from __future__ import annotations

from typing import Any

CAT_CLASS = 15  # COCO class index for "cat"


class CatDetector:
    """
    用 YOLOv8/YOLO11 检测帧中的猫。

    detect() 返回每只猫的 bbox、置信度、中心点、宽高。
    """

    def __init__(self, model_path: str = "yolo11n.pt", conf: float = 0.4):
        from ultralytics import YOLO

        self.model = YOLO(model_path)
        self.conf = conf

    def detect(self, frame: Any) -> list[dict]:
        """
        frame: BGR numpy 数组（来自 cv2 / HunterAPI.snapshot()）

        返回列表，每项：
            {
                "bbox": (x1, y1, x2, y2),  # 像素坐标
                "conf": float,
                "cx":   float,             # bbox 中心 x
                "cy":   float,             # bbox 中心 y
                "w":    float,             # bbox 宽
                "h":    float,             # bbox 高
            }
        """
        results = self.model(frame, conf=self.conf, classes=[CAT_CLASS], verbose=False)
        out: list[dict] = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                out.append(
                    {
                        "bbox": (x1, y1, x2, y2),
                        "conf": float(box.conf[0]),
                        "cx": (x1 + x2) / 2,
                        "cy": (y1 + y2) / 2,
                        "w": x2 - x1,
                        "h": y2 - y1,
                    }
                )
        return out
