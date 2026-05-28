import unittest


class NextSessionPlanTest(unittest.TestCase):
    def test_safe_pause_strategy_creates_low_intensity_recovery_plan(self):
        from src.app.next_session_plan import build_next_session_plan

        plan = build_next_session_plan(
            {"preferred_arm": "wand_slow", "play_style": "谨慎探索型"},
            {"decision": "safe_pause"},
            {"recommended_arm": "laser_escape", "source": "memory"},
        )

        self.assertEqual(plan["recommended_arm"], "wand_slow")
        self.assertEqual(plan["scenario_focus"], "lost_target")
        self.assertEqual(plan["intensity"], "low")
        self.assertIn("慢速", plan["operator_note"])

    def test_continue_engagement_uses_personalized_arm(self):
        from src.app.next_session_plan import build_next_session_plan

        plan = build_next_session_plan(
            {"preferred_arm": "wand_hover", "play_style": "稳定观察型"},
            {"decision": "continue_engagement"},
            {"recommended_arm": "laser_escape", "source": "memory"},
        )

        self.assertEqual(plan["recommended_arm"], "laser_escape")
        self.assertEqual(plan["scenario_focus"], "approach")
        self.assertEqual(plan["intensity"], "medium")

    def test_search_again_uses_observation_plan(self):
        from src.app.next_session_plan import build_next_session_plan

        plan = build_next_session_plan(
            {"preferred_arm": "wand_slow", "play_style": "慢热试探型"},
            {"decision": "search_again"},
            {"recommended_arm": "wand_slow", "source": "default"},
        )

        self.assertEqual(plan["scenario_focus"], "empty")
        self.assertEqual(plan["intensity"], "low")
        self.assertIn("重新搜索", plan["operator_note"])


if __name__ == "__main__":
    unittest.main()
