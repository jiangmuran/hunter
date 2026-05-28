import unittest


class CatProfileTest(unittest.TestCase):
    def test_profile_summarizes_engagement_preference_and_risk(self):
        from src.app.cat_profile import build_cat_profile

        profile = build_cat_profile(
            [
                {"summary": {"activity": {"engagement_score": 80}}, "report": {"outcome": "success"}},
                {"summary": {"activity": {"engagement_score": 40}}, "report": {"outcome": "lost_target"}},
            ],
            [{"arm": "wand_slow", "expected_reward": 0.8}],
        )

        self.assertEqual(profile["engagement_level"], "medium")
        self.assertEqual(profile["preferred_arm"], "wand_slow")
        self.assertEqual(profile["play_style"], "谨慎探索型")
        self.assertIn("lost_target", profile["risk_flags"])
        self.assertIn("慢速", profile["summary"])

    def test_profile_uses_default_when_history_is_empty(self):
        from src.app.cat_profile import build_cat_profile

        profile = build_cat_profile([], [])

        self.assertEqual(profile["engagement_level"], "unknown")
        self.assertEqual(profile["preferred_arm"], "wand_slow")
        self.assertEqual(profile["risk_flags"], [])
        self.assertIn("还没有", profile["summary"])

    def test_profile_marks_high_engagement_as_active(self):
        from src.app.cat_profile import build_cat_profile

        profile = build_cat_profile(
            [{"summary": {"activity": {"engagement_score": 90}}, "report": {"outcome": "success"}}],
            [{"arm": "laser_escape", "expected_reward": 0.9}],
        )

        self.assertEqual(profile["engagement_level"], "high")
        self.assertEqual(profile["play_style"], "主动追逐型")
        self.assertEqual(profile["preferred_arm"], "laser_escape")


if __name__ == "__main__":
    unittest.main()
