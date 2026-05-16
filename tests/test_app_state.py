import unittest


class AppStateTest(unittest.TestCase):
    def test_snapshot_defaults_to_idle_and_serializes(self):
        from src.app.state import AppState, RuntimeState

        state = AppState()

        self.assertEqual(state.current, RuntimeState.IDLE)
        self.assertEqual(state.tick, 0)
        self.assertIsNone(state.last_action)
        data = state.to_dict()
        self.assertEqual(data["state"], "idle")
        self.assertEqual(data["tick"], 0)
        self.assertIn("target", data)
        self.assertIn("healthy", data)
        self.assertIn("error", data)

    def test_state_snapshot_includes_target_and_action(self):
        from src.app.state import AppState, RuntimeState

        state = AppState(
            current=RuntimeState.ALIGNING,
            tick=3,
            last_action="rotate_cw",
            target={"center_offset_x": 0.4},
            healthy=True,
        )

        data = state.to_dict()
        self.assertEqual(data["state"], "aligning")
        self.assertEqual(data["last_action"], "rotate_cw")
        self.assertEqual(data["target"], {"center_offset_x": 0.4})


if __name__ == "__main__":
    unittest.main()
