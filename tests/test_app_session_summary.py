import unittest


class SessionSummaryTest(unittest.TestCase):
    def test_summary_handles_empty_session_gracefully(self):
        from src.app.session_summary import summarize_session

        summary = summarize_session([], [])

        self.assertEqual(summary["ticks"], 0)
        self.assertIsNone(summary["final_state"])
        self.assertFalse(summary["target_seen"])
        self.assertFalse(summary["lost_target"])
        self.assertEqual(summary["state_counts"], {})
        self.assertEqual(summary["command_counts"], {})

    def test_summary_uses_last_state_as_final_state(self):
        from src.app.session_summary import summarize_session

        states = [
            {"state": "scanning", "tick": 1, "last_action": "stop", "healthy": True, "error": None},
            {"state": "approaching", "tick": 2, "last_action": "forward", "healthy": True, "error": None},
        ]

        summary = summarize_session(states, [])

        self.assertEqual(summary["ticks"], 2)
        self.assertEqual(summary["final_state"], "approaching")
        self.assertEqual(summary["last_action"], "forward")
        self.assertTrue(summary["healthy"])
        self.assertIsNone(summary["error"])

    def test_summary_counts_states_and_commands(self):
        from src.app.events import AppEvent, EventKind
        from src.app.session_summary import summarize_session

        states = [
            {"state": "scanning", "tick": 1},
            {"state": "aligning", "tick": 2},
            {"state": "aligning", "tick": 3},
        ]
        events = [
            AppEvent(EventKind.COMMAND, 1, "stop"),
            AppEvent(EventKind.COMMAND, 2, "rotate_cw"),
            {"kind": "command", "tick": 3, "message": "rotate_cw"},
        ]

        summary = summarize_session(states, events)

        self.assertEqual(summary["state_counts"], {"scanning": 1, "aligning": 2})
        self.assertEqual(summary["command_counts"], {"stop": 1, "rotate_cw": 2})

    def test_summary_marks_target_seen_and_stop_distance_success(self):
        from src.app.session_summary import summarize_session

        states = [
            {"state": "scanning", "tick": 1, "target": None},
            {"state": "approaching", "tick": 2, "target": {"cx": 320, "cy": 240}},
            {"state": "at_stop_distance", "tick": 3, "target": {"cx": 320, "cy": 240}},
        ]

        summary = summarize_session(states, [])

        self.assertTrue(summary["target_seen"])
        self.assertTrue(summary["reached_stop_distance"])
        self.assertIn("target acquired during session", summary["highlights"])
        self.assertIn("approached target and stopped at safe distance", summary["highlights"])

    def test_summary_marks_lost_target_and_error(self):
        from src.app.session_summary import summarize_session

        states = [
            {"state": "lost_target", "tick": 1, "error": None},
            {"state": "error", "tick": 2, "error": "detector failed", "healthy": False},
        ]

        summary = summarize_session(states, [])

        self.assertTrue(summary["lost_target"])
        self.assertEqual(summary["error"], "detector failed")
        self.assertFalse(summary["healthy"])
        self.assertIn("target was lost after acquisition", summary["highlights"])
        self.assertIn("session ended in error", summary["highlights"])

    def test_summarize_session_includes_trajectory_metrics(self):
        from src.app.session_summary import summarize_session

        states = [
            {"state": "aligning", "target": {"cx": 100, "cy": 200}, "healthy": True, "last_action": "rotate_cw"},
            {"state": "approaching", "target": {"cx": 140, "cy": 220}, "healthy": True, "last_action": "forward"},
            {"state": "at_stop_distance", "target": {"cx": 180, "cy": 260}, "healthy": True, "last_action": "stop"},
        ]

        summary = summarize_session(states, [])

        self.assertEqual(summary["trajectory"]["points"], [(100, 200), (140, 220), (180, 260)])
        self.assertEqual(summary["trajectory"]["point_count"], 3)
        self.assertEqual(summary["trajectory"]["path_length"], 101.29)
        self.assertEqual(summary["activity"]["target_visible_ticks"], 3)
        self.assertEqual(summary["activity"]["moving_ticks"], 2)
        self.assertEqual(summary["activity"]["engagement_score"], 100)

    def test_summarize_session_handles_missing_target_trajectory(self):
        from src.app.session_summary import summarize_session

        states = [
            {"state": "scanning", "target": None, "healthy": True, "last_action": "stop"},
            {"state": "scanning", "target": None, "healthy": True, "last_action": "stop"},
        ]

        summary = summarize_session(states, [])

        self.assertEqual(summary["trajectory"]["points"], [])
        self.assertEqual(summary["trajectory"]["point_count"], 0)
        self.assertEqual(summary["trajectory"]["path_length"], 0)
        self.assertEqual(summary["activity"]["target_visible_ticks"], 0)
        self.assertEqual(summary["activity"]["moving_ticks"], 0)
        self.assertEqual(summary["activity"]["engagement_score"], 0)


    def test_summarize_session_handles_partial_target_data(self):
        from src.app.session_summary import summarize_session

        states = [
            {"state": "aligning", "target": {"cx": 100}, "healthy": True, "last_action": "rotate_cw"},
            {"state": "approaching", "target": {"cx": 140, "cy": 220}, "healthy": True, "last_action": "forward"},
            {"state": "scanning", "target": {"cy": 260}, "healthy": True, "last_action": "stop"},
        ]

        summary = summarize_session(states, [])

        self.assertEqual(summary["trajectory"]["points"], [(140, 220)])
        self.assertEqual(summary["trajectory"]["point_count"], 1)
        self.assertEqual(summary["activity"]["target_visible_ticks"], 1)
        self.assertEqual(summary["activity"]["engagement_score"], 33)
        # complete target exists, so target_seen and highlights are set
        self.assertTrue(summary["target_seen"])
        self.assertIn("target acquired during session", summary["highlights"])

    def test_summarize_session_partial_targets_only_not_seen(self):
        from src.app.session_summary import summarize_session

        states = [
            {"state": "aligning", "target": {"cx": 100}, "healthy": True, "last_action": "rotate_cw"},
            {"state": "scanning", "target": {"cy": 260}, "healthy": True, "last_action": "stop"},
        ]

        summary = summarize_session(states, [])

        self.assertEqual(summary["trajectory"]["points"], [])
        self.assertEqual(summary["trajectory"]["point_count"], 0)
        self.assertEqual(summary["activity"]["target_visible_ticks"], 0)
        self.assertEqual(summary["activity"]["engagement_score"], 0)
        self.assertFalse(summary["target_seen"])
        self.assertNotIn("target acquired during session", summary["highlights"])


if __name__ == "__main__":
    unittest.main()
