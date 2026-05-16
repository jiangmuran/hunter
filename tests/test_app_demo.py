import unittest


class DemoTest(unittest.TestCase):
    def test_mock_demo_constructs_without_hardware(self):
        from src.app.demo import build_orchestrator
        from src.app.mock_api import MockHunterAPI

        orchestrator = build_orchestrator(mode="mock", ticks=1)

        self.assertIsInstance(orchestrator.api, MockHunterAPI)

    def test_real_demo_selects_hunter_api_only_when_requested(self):
        from src.app.demo import build_api
        from src.api_client import HunterAPI

        api = build_api(mode="real", base_url="http://example.test")

        self.assertIsInstance(api, HunterAPI)
        self.assertEqual(api.base, "http://example.test")

    def test_cli_argument_parsing_supports_mode_base_url_and_ticks(self):
        from src.app.demo import parse_args

        args = parse_args(["--mode", "mock", "--base-url", "http://robot.local", "--ticks", "3"])

        self.assertEqual(args.mode, "mock")
        self.assertEqual(args.base_url, "http://robot.local")
        self.assertEqual(args.ticks, 3)

    def test_mock_demo_runs_finite_ticks(self):
        from src.app.demo import run_demo

        states = run_demo(["--mode", "mock", "--ticks", "2"], verbose=False)

        self.assertEqual(len(states), 2)
        self.assertEqual(states[-1]["tick"], 2)


if __name__ == "__main__":
    unittest.main()
