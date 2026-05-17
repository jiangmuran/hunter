import unittest


class PersonalizationPolicyTest(unittest.TestCase):
    def test_recommends_default_arm_without_memory_box(self):
        from src.app.personalization_policy import recommend_play_arm

        recommendation = recommend_play_arm(memory_box=None)

        self.assertEqual(recommendation, {
            "arm": "wand_slow",
            "source": "default",
            "expected_reward": None,
        })

    def test_recommends_top_memory_preference(self):
        from src.app.personalization_policy import recommend_play_arm

        recommendation = recommend_play_arm(FakeMemoryBox([
            ("laser_escape", 0.8),
            ("wand_hover", 0.6),
        ]))

        self.assertEqual(recommendation, {
            "arm": "laser_escape",
            "source": "memory",
            "expected_reward": 0.8,
        })

    def test_ignores_empty_memory_preferences(self):
        from src.app.personalization_policy import recommend_play_arm

        recommendation = recommend_play_arm(FakeMemoryBox([]), default_arm="wand_hover")

        self.assertEqual(recommendation, {
            "arm": "wand_hover",
            "source": "default",
            "expected_reward": None,
        })

    def test_build_personalization_preview_includes_preferences(self):
        from src.app.personalization_policy import build_personalization_preview

        preview = build_personalization_preview(FakeMemoryBox([
            ("laser_escape", 0.8),
            ("wand_hover", 0.6),
        ]), limit=2)

        self.assertEqual(preview["recommended_arm"], "laser_escape")
        self.assertEqual(preview["source"], "memory")
        self.assertEqual(preview["preferences"], [
            {"arm": "laser_escape", "expected_reward": 0.8},
            {"arm": "wand_hover", "expected_reward": 0.6},
        ])


class FakeMemoryBox:
    def __init__(self, preferences):
        self.preferences = preferences

    def top_preferences(self, limit):
        return self.preferences[:limit]


if __name__ == "__main__":
    unittest.main()
