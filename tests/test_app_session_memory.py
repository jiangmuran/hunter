import unittest


class SessionMemoryTest(unittest.TestCase):
    def test_session_summary_maps_success_to_positive_reward(self):
        from src.app.session_memory import session_memory_update

        update = session_memory_update({
            "reached_stop_distance": True,
            "lost_target": False,
            "error": None,
            "command_counts": {"forward": 1, "rotate_cw": 1, "stop": 2},
        })

        self.assertEqual(update, {"arm": "approach", "reward": 1, "reason": "reached_stop_distance"})

    def test_session_summary_maps_lost_target_to_negative_tracking_reward(self):
        from src.app.session_memory import session_memory_update

        update = session_memory_update({
            "reached_stop_distance": False,
            "lost_target": True,
            "error": None,
            "command_counts": {"forward": 4, "stop": 3},
        })

        self.assertEqual(update, {"arm": "track_target", "reward": 0, "reason": "lost_target"})

    def test_session_summary_maps_error_to_negative_safety_reward(self):
        from src.app.session_memory import session_memory_update

        update = session_memory_update({
            "reached_stop_distance": False,
            "lost_target": False,
            "error": "mock detector failed",
            "command_counts": {"stop": 3},
        })

        self.assertEqual(update, {"arm": "safe_stop", "reward": 0, "reason": "error"})

    def test_session_summary_maps_no_target_to_none(self):
        from src.app.session_memory import session_memory_update

        update = session_memory_update({
            "reached_stop_distance": False,
            "lost_target": False,
            "error": None,
            "target_seen": False,
            "command_counts": {"stop": 3},
        })

        self.assertIsNone(update)

    def test_demo_session_can_include_memory_update(self):
        from src.app.demo import run_demo_session

        session = run_demo_session([
            "--mode", "mock",
            "--scenario", "approach",
            "--ticks", "4",
            "--include-memory-update",
        ], verbose=False)

        self.assertEqual(session["memory_update"]["arm"], "approach")
        self.assertEqual(session["memory_update"]["reward"], 1)
    def test_apply_session_memory_update_updates_memory_box(self):
        from src.app.session_memory import apply_session_memory_update

        memory_box = FakeMemoryBox()

        result = apply_session_memory_update({"reached_stop_distance": True}, memory_box)

        self.assertEqual(memory_box.updates, [("laser_escape", 1)])
        self.assertEqual(result, {
            "app_arm": "approach",
            "memory_arm": "laser_escape",
            "reward": 1,
            "reason": "reached_stop_distance",
        })

    def test_apply_session_memory_update_skips_no_target(self):
        from src.app.session_memory import apply_session_memory_update

        memory_box = FakeMemoryBox()

        result = apply_session_memory_update({"target_seen": False}, memory_box)

        self.assertIsNone(result)
        self.assertEqual(memory_box.updates, [])

    def test_memory_preferences_are_exposed_as_dicts(self):
        from src.app.session_memory import memory_preferences

        memory_box = FakeMemoryBox(preferences=[("laser_escape", 0.75), ("wand_hover", 0.6)])

        preferences = memory_preferences(memory_box, limit=2)

        self.assertEqual(preferences, [
            {"arm": "laser_escape", "expected_reward": 0.75},
            {"arm": "wand_hover", "expected_reward": 0.6},
        ])


class FakeMemoryBox:
    def __init__(self, preferences=None):
        self.updates = []
        self.preferences = preferences or []

    def update(self, arm, reward):
        self.updates.append((arm, reward))

    def top_preferences(self, limit):
        return self.preferences[:limit]


if __name__ == "__main__":
    unittest.main()
