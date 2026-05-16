import unittest


class AppConfigTest(unittest.TestCase):
    def test_defaults_to_mock_mode(self):
        from src.app.config import AppConfig

        config = AppConfig()

        self.assertEqual(config.mode, "mock")
        self.assertEqual(config.base_url, "http://192.168.0.170:8000")
        self.assertGreater(config.tick_interval, 0)
        self.assertGreater(config.align_threshold, 0)
        self.assertGreater(config.stop_size_ratio, 0)

    def test_real_mode_keeps_base_url(self):
        from src.app.config import AppConfig

        config = AppConfig(mode="real", base_url="http://robot.local:8000")

        self.assertEqual(config.mode, "real")
        self.assertEqual(config.base_url, "http://robot.local:8000")


if __name__ == "__main__":
    unittest.main()
