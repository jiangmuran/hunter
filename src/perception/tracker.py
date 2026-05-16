from __future__ import annotations

from math import hypot
from typing import Any


class CatTracker:
    def __init__(self, frame_size: tuple[int, int], retention_distance: float = 120.0):
        self.frame_width, self.frame_height = frame_size
        self.retention_distance = retention_distance
        self.target: dict[str, Any] | None = None

    def update(self, detections: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not detections:
            if self.target is None:
                return None
            self.target = {**self.target, "missing": True, "missing_count": self.target.get("missing_count", 0) + 1}
            return self.target

        selected = self._select_detection(detections)
        self.target = self._normalize({**selected, "missing": False, "missing_count": 0})
        return self.target

    def _select_detection(self, detections: list[dict[str, Any]]) -> dict[str, Any]:
        if self.target is not None:
            nearby = [
                detection
                for detection in detections
                if hypot(detection["cx"] - self.target["cx"], detection["cy"] - self.target["cy"])
                <= self.retention_distance
            ]
            if nearby:
                return min(
                    nearby,
                    key=lambda detection: hypot(
                        detection["cx"] - self.target["cx"], detection["cy"] - self.target["cy"]
                    ),
                )
        return max(detections, key=lambda detection: detection["w"] * detection["h"])

    def _normalize(self, target: dict[str, Any]) -> dict[str, Any]:
        target["center_offset_x"] = (target["cx"] - self.frame_width / 2) / self.frame_width
        target["size_ratio"] = (target["w"] * target["h"]) / (self.frame_width * self.frame_height)
        return target
