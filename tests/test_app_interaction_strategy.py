import unittest


class InteractionStrategyTest(unittest.TestCase):
    def test_success_session_continues_engagement(self):
        from src.app.interaction_strategy import build_interaction_strategy

        strategy = build_interaction_strategy({
            "reached_stop_distance": True,
            "activity": {"engagement_score": 75},
            "healthy": True,
        })

        self.assertEqual(strategy["decision"], "continue_engagement")
        self.assertEqual(strategy["confidence"], "high")
        self.assertIn("安全靠近", strategy["reason"])

    def test_no_target_searches_again(self):
        from src.app.interaction_strategy import build_interaction_strategy

        strategy = build_interaction_strategy({"target_seen": False, "healthy": True})

        self.assertEqual(strategy["decision"], "search_again")
        self.assertEqual(strategy["confidence"], "medium")
        self.assertIn("重新搜索", strategy["next_action"])

    def test_lost_target_uses_safe_pause(self):
        from src.app.interaction_strategy import build_interaction_strategy

        strategy = build_interaction_strategy({"lost_target": True, "healthy": True})

        self.assertEqual(strategy["decision"], "safe_pause")
        self.assertIn("保守暂停", strategy["next_action"])

    def test_error_uses_safe_pause(self):
        from src.app.interaction_strategy import build_interaction_strategy

        strategy = build_interaction_strategy({"error": "detector failed", "healthy": False})

        self.assertEqual(strategy["decision"], "safe_pause")
        self.assertEqual(strategy["confidence"], "high")
        self.assertIn("异常", strategy["reason"])

    def test_history_with_repeated_poor_outcomes_requests_recovery_check(self):
        from src.app.interaction_strategy import build_suite_strategy

        strategy = build_suite_strategy([
            {"report": {"outcome": "lost_target"}},
            {"report": {"outcome": "error"}},
            {"report": {"outcome": "no_target"}},
        ])

        self.assertEqual(strategy["decision"], "recovery_check")
        self.assertEqual(strategy["confidence"], "high")
        self.assertIn("连续", strategy["reason"])


if __name__ == "__main__":
    unittest.main()
