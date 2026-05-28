import unittest

from src.app.surprise_entropy import build_surprise_entropy_preview


class SurpriseEntropyTest(unittest.TestCase):
    def test_high_engagement_continue_engagement_prefers_personalized_active_action(self):
        result = build_surprise_entropy_preview(
            profile={"engagement_level": "high", "preferred_arm": "wand_slow", "risk_flags": []},
            strategy={"decision": "continue_engagement"},
            personalization={"recommended_arm": "laser_escape"},
            recent_actions=[],
        )

        self.assertEqual(result["selected_action"]["name"], "laser_escape_short")
        self.assertEqual(result["selected_action"]["arm"], "laser_escape")
        self.assertNotEqual(result["selected_action"]["name"], "pause_observe")

    def test_safe_pause_selects_observation_and_closes_safety_gate(self):
        result = build_surprise_entropy_preview(
            profile={"engagement_level": "high", "preferred_arm": "laser_escape", "risk_flags": []},
            strategy={"decision": "safe_pause"},
            personalization={"recommended_arm": "laser_escape"},
        )

        self.assertFalse(result["safety_gate"]["allowed"])
        self.assertEqual(result["selected_action"]["name"], "pause_observe")

    def test_repeated_recent_action_lowers_candidate_novelty(self):
        result = build_surprise_entropy_preview(
            profile={"engagement_level": "high", "preferred_arm": "laser_escape", "risk_flags": []},
            strategy={"decision": "continue_engagement"},
            personalization={"recommended_arm": "laser_escape"},
            recent_actions=["laser_escape_short", "laser_escape_short", "laser_escape_short"],
        )

        laser = _candidate(result, "laser_escape_short")
        wand = _candidate(result, "wand_slow_sweep")
        self.assertLess(laser["novelty"], wand["novelty"])

    def test_lost_target_risk_penalizes_high_intensity_actions(self):
        result = build_surprise_entropy_preview(
            profile={"engagement_level": "high", "preferred_arm": "laser_escape", "risk_flags": ["lost_target"]},
            strategy={"decision": "continue_engagement"},
            personalization={"recommended_arm": "laser_escape"},
        )

        laser = _candidate(result, "laser_escape_short")
        self.assertGreater(laser["risk_penalty"], 0)
        self.assertFalse(laser["safety_allowed"])
        self.assertIn(result["selected_action"]["intensity"], ["low", "medium"])

    def test_preview_contains_expected_shape(self):
        result = build_surprise_entropy_preview(
            profile={"engagement_level": "medium", "preferred_arm": "wand_slow", "risk_flags": []},
            strategy={"decision": "search_again"},
            personalization={},
            recent_outcomes=["success", "lost_target"],
        )

        self.assertEqual(result["engine"], "surprise_entropy")
        self.assertIn("selected_action", result)
        self.assertIn("candidates", result)
        self.assertIn("safety_gate", result)
        self.assertEqual(result["recent_outcomes"], ["success", "lost_target"])
        self.assertEqual(len(result["candidates"]), 5)


def _candidate(result, name):
    return next(candidate for candidate in result["candidates"] if candidate["name"] == name)


if __name__ == "__main__":
    unittest.main()
