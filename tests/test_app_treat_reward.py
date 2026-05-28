import unittest

from src.app.treat_reward import build_treat_reward_decision, build_treat_reward_preview


class TreatRewardTest(unittest.TestCase):
    def test_successful_catch_allows_reward(self):
        result = build_treat_reward_decision(
            {"reached_stop_distance": True, "healthy": True, "activity": {"engagement_score": 85}},
            {"daily_limit": 12, "dispensed_today": 3, "remaining_treats": 20},
        )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["action"], "dispense_treat")
        self.assertEqual(result["recommended_amount"], 2)
        self.assertEqual(result["drop_target_cm"], 20)

    def test_no_catch_success_blocks_reward(self):
        result = build_treat_reward_decision(
            {"reached_stop_distance": False, "healthy": True},
            {"daily_limit": 12, "dispensed_today": 0, "remaining_treats": 20},
        )

        self.assertFalse(result["allowed"])
        self.assertIn("no_catch_success", result["blocked_reasons"])

    def test_daily_limit_blocks_reward(self):
        result = build_treat_reward_decision(
            {"final_state": "at_stop_distance", "healthy": True},
            {"daily_limit": 3, "dispensed_today": 3, "remaining_treats": 20},
        )

        self.assertFalse(result["allowed"])
        self.assertIn("daily_limit_reached", result["blocked_reasons"])

    def test_empty_dispenser_blocks_reward(self):
        result = build_treat_reward_decision(
            {"final_state": "at_stop_distance", "healthy": True},
            {"daily_limit": 12, "dispensed_today": 3, "remaining_treats": 0},
        )

        self.assertFalse(result["allowed"])
        self.assertIn("empty_dispenser", result["blocked_reasons"])

    def test_lost_target_blocks_reward(self):
        result = build_treat_reward_decision(
            {"final_state": "at_stop_distance", "healthy": True, "lost_target": True},
            {"daily_limit": 12, "dispensed_today": 3, "remaining_treats": 20},
        )

        self.assertFalse(result["allowed"])
        self.assertIn("target_lost", result["blocked_reasons"])

    def test_unhealthy_session_blocks_reward_even_after_catch_success(self):
        result = build_treat_reward_decision(
            {"final_state": "at_stop_distance", "healthy": False},
            {"daily_limit": 12, "dispensed_today": 0, "remaining_treats": 20},
        )

        self.assertFalse(result["allowed"])
        self.assertIn("session_unhealthy", result["blocked_reasons"])

    def test_error_session_blocks_reward_even_after_catch_success(self):
        result = build_treat_reward_decision(
            {"final_state": "at_stop_distance", "healthy": True, "error": True},
            {"daily_limit": 12, "dispensed_today": 0, "remaining_treats": 20},
        )

        self.assertFalse(result["allowed"])
        self.assertIn("session_unhealthy", result["blocked_reasons"])

    def test_treat_reward_preview_contains_required_cases(self):
        result = build_treat_reward_preview()

        self.assertEqual(result["capability"], "treat_reward_policy")
        self.assertIn("successful_catch", result["cases"])
        self.assertIn("daily_limit_reached", result["cases"])
        self.assertTrue(result["cases"]["successful_catch"]["allowed"])
        self.assertFalse(result["cases"]["daily_limit_reached"]["allowed"])


if __name__ == "__main__":
    unittest.main()
