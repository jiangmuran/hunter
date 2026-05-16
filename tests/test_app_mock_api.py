import unittest


class MockHunterAPITest(unittest.TestCase):
    def test_health_and_state_are_robot_like(self):
        from src.app.mock_api import MockHunterAPI

        api = MockHunterAPI(healthy=True)

        self.assertTrue(api.health()["modules"]["mock"]["ok"])
        state = api.state()
        self.assertIn("state", state)
        self.assertIn("logs", state)
        self.assertEqual(state["state"]["last_action"], "STOP")

    def test_commands_are_recorded(self):
        from src.app.mock_api import MockHunterAPI

        api = MockHunterAPI()

        api.move("forward")
        api.rotate(clockwise=False)
        api.stop()
        api.play_cat_sound(2)

        self.assertEqual(
            [entry["action"] for entry in api.command_history],
            ["forward", "rotate_ccw", "stop", "cat2"],
        )
        self.assertEqual(api.state()["state"]["last_action"], "CAT2")

    def test_unhealthy_mock_reports_false(self):
        from src.app.mock_api import MockHunterAPI

        api = MockHunterAPI(healthy=False)

        self.assertFalse(api.health()["modules"]["mock"]["ok"])

    def test_mock_exposes_hunter_api_surface(self):
        from src.app.mock_api import MockHunterAPI

        api = MockHunterAPI()
        for name in [
            "snapshot", "stream_url", "cmd", "move", "rotate", "stop",
            "emergency", "play_cat_sound", "record_start", "record_stop",
            "play_wav", "state", "health",
        ]:
            self.assertTrue(callable(getattr(api, name)))


if __name__ == "__main__":
    unittest.main()
