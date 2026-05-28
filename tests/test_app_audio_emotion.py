import unittest

from src.app.audio_emotion import build_audio_emotion_preview, classify_audio_emotion


class AudioEmotionTest(unittest.TestCase):
    def test_alert_audio_classification_uses_pitch_and_energy(self):
        result = classify_audio_emotion({"pitch_hz": 720, "energy": 0.9, "duration_ms": 300})

        self.assertEqual(result["label"], "alert")
        self.assertEqual(result["display_label"], "警戒")
        self.assertIn("暂停互动", result["recommended_response"])

    def test_hungry_audio_classification_uses_long_energetic_meow(self):
        result = classify_audio_emotion({"pitch_hz": 360, "energy": 0.7, "duration_ms": 1200})

        self.assertEqual(result["label"], "hungry")
        self.assertEqual(result["display_label"], "饥饿")

    def test_clingy_audio_classification_uses_repetition(self):
        result = classify_audio_emotion({"pitch_hz": 500, "energy": 0.4, "duration_ms": 400, "repetition": 4})

        self.assertEqual(result["label"], "clingy")
        self.assertEqual(result["display_label"], "撒娇")

    def test_default_audio_classification_is_playful(self):
        result = classify_audio_emotion({"pitch_hz": 420, "energy": 0.4, "duration_ms": 300})

        self.assertEqual(result["label"], "playful")
        self.assertEqual(result["display_label"], "玩耍")

    def test_audio_emotion_preview_covers_prd_labels(self):
        result = build_audio_emotion_preview()

        self.assertEqual(result["capability"], "audio_emotion_classifier")
        self.assertEqual(set(result["labels"]), {"hungry", "clingy", "alert", "playful"})
        self.assertEqual(len(result["classifications"]), 4)
        self.assertIn(result["dominant_emotion"], result["labels"])


if __name__ == "__main__":
    unittest.main()
