from __future__ import annotations

from typing import Any


EMOTION_LABELS = {
    "hungry": "饥饿",
    "clingy": "撒娇",
    "alert": "警戒",
    "playful": "玩耍",
}


def classify_audio_emotion(audio_features: dict[str, Any] | None = None) -> dict[str, Any]:
    features = audio_features or {}
    label = _label_from_features(features)
    confidence = _confidence_for(label, features)
    return {
        "label": label,
        "display_label": EMOTION_LABELS[label],
        "confidence": confidence,
        "source": "software_audio_features",
        "features": dict(features),
        "recommended_response": _recommended_response(label),
    }


def build_audio_emotion_preview(samples: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    input_samples = samples or _default_samples()
    classifications = [classify_audio_emotion(sample) for sample in input_samples]
    counts = {}
    for item in classifications:
        counts[item["label"]] = counts.get(item["label"], 0) + 1
    dominant = max(counts, key=counts.get) if counts else "playful"
    return {
        "capability": "audio_emotion_classifier",
        "labels": list(EMOTION_LABELS.keys()),
        "classifications": classifications,
        "emotion_counts": counts,
        "dominant_emotion": dominant,
        "summary": f"识别到主要叫声情绪：{EMOTION_LABELS[dominant]}。",
    }


def _label_from_features(features: dict[str, Any]) -> str:
    explicit = features.get("label")
    if explicit in EMOTION_LABELS:
        return explicit
    pitch = _number(features.get("pitch_hz"), 0)
    energy = _number(features.get("energy"), 0)
    duration = _number(features.get("duration_ms"), 0)
    repetition = _number(features.get("repetition"), 1)
    if energy >= 0.8 and pitch >= 650:
        return "alert"
    if duration >= 900 and energy >= 0.55:
        return "hungry"
    if repetition >= 3 and pitch >= 450:
        return "clingy"
    return "playful"


def _confidence_for(label: str, features: dict[str, Any]) -> float:
    explicit_confidence = features.get("confidence")
    if isinstance(explicit_confidence, int | float):
        return round(max(0.0, min(1.0, float(explicit_confidence))), 2)
    if label == "alert":
        return 0.86
    if label == "hungry":
        return 0.82
    if label == "clingy":
        return 0.78
    return 0.74


def _recommended_response(label: str) -> str:
    if label == "alert":
        return "暂停互动并降低动作强度。"
    if label == "hungry":
        return "记录饥饿信号，等待投喂策略判断。"
    if label == "clingy":
        return "使用低强度陪伴动作安抚。"
    return "可以继续轻量玩耍互动。"


def _default_samples() -> list[dict[str, Any]]:
    return [
        {"pitch_hz": 720, "energy": 0.9, "duration_ms": 320, "repetition": 1},
        {"pitch_hz": 380, "energy": 0.7, "duration_ms": 1100, "repetition": 1},
        {"pitch_hz": 520, "energy": 0.45, "duration_ms": 420, "repetition": 4},
        {"pitch_hz": 430, "energy": 0.42, "duration_ms": 360, "repetition": 1},
    ]


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, int | float) else default
