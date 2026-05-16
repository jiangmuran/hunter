import unittest


class DemoTest(unittest.TestCase):
    def test_mock_demo_constructs_without_hardware(self):
        from src.app.demo import build_orchestrator
        from src.app.mock_api import MockHunterAPI

        orchestrator = build_orchestrator(mode="mock", ticks=1)

        self.assertIsInstance(orchestrator.api, MockHunterAPI)

    def test_real_demo_selects_hunter_api_only_when_requested(self):
        from src.app.demo import build_api
        from src.software.api_client import HunterAPI

        api = build_api(mode="real", base_url="http://example.test")

        self.assertIsInstance(api, HunterAPI)
        self.assertEqual(api.base, "http://example.test")

    def test_cli_argument_parsing_supports_mode_base_url_ticks_and_scenario(self):
        from src.app.demo import parse_args

        args = parse_args([
            "--mode",
            "mock",
            "--base-url",
            "http://robot.local",
            "--ticks",
            "4",
            "--scenario",
            "approach",
        ])

        self.assertEqual(args.mode, "mock")
        self.assertEqual(args.base_url, "http://robot.local")
        self.assertEqual(args.ticks, 4)
        self.assertEqual(args.scenario, "approach")

    def test_mock_demo_runs_finite_ticks(self):
        from src.app.demo import run_demo

        states = run_demo(["--mode", "mock", "--ticks", "2"], verbose=False)

        self.assertEqual(len(states), 2)
        self.assertEqual(states[-1]["tick"], 2)

    def test_run_demo_still_returns_state_list(self):
        from src.app.demo import run_demo

        states = run_demo(["--mode", "mock", "--scenario", "approach", "--ticks", "4"], verbose=False)

        self.assertIsInstance(states, list)
        self.assertEqual(states[-1]["state"], "at_stop_distance")

    def test_run_demo_session_returns_states_events_and_summary(self):
        from src.app.demo import run_demo_session

        session = run_demo_session(["--mode", "mock", "--scenario", "approach", "--ticks", "4"], verbose=False)

        self.assertIn("states", session)
        self.assertIn("events", session)
        self.assertIn("summary", session)
        self.assertEqual(session["summary"]["final_state"], "at_stop_distance")
        self.assertEqual(session["summary"]["command_counts"]["rotate_cw"], 1)
        self.assertEqual(session["summary"]["command_counts"]["forward"], 1)

    def test_mock_demo_defaults_to_empty_scenario(self):
        from src.app.demo import run_demo

        states = run_demo(["--mode", "mock", "--ticks", "2"], verbose=False)

        self.assertEqual([state["state"] for state in states], ["scanning", "scanning"])
        self.assertEqual([state["last_action"] for state in states], ["stop", "stop"])

    def test_mock_demo_approach_scenario_runs_full_flow(self):
        from src.app.demo import run_demo

        states = run_demo(["--mode", "mock", "--scenario", "approach", "--ticks", "4"], verbose=False)

        self.assertEqual(
            [state["state"] for state in states],
            ["scanning", "aligning", "approaching", "at_stop_distance"],
        )
        self.assertEqual(
            [state["last_action"] for state in states],
            ["stop", "rotate_cw", "forward", "stop"],
        )

    def test_build_orchestrator_accepts_approach_scenario_without_hardware(self):
        from src.app.demo import build_orchestrator
        from src.app.mock_api import MockHunterAPI

        orchestrator = build_orchestrator(mode="mock", scenario="approach")
        for _ in range(4):
            orchestrator.tick()

        self.assertIsInstance(orchestrator.api, MockHunterAPI)
        actions = [entry["action"] for entry in orchestrator.api.command_history]
        self.assertIn("rotate_cw", actions)
        self.assertIn("forward", actions)

    def test_mock_demo_lost_target_scenario_stops_after_losing_target(self):
        from src.app.demo import run_demo_session

        session = run_demo_session(["--mode", "mock", "--scenario", "lost_target", "--ticks", "7"], verbose=False)
        states = session["states"]

        self.assertEqual(states[-1]["state"], "lost_target")
        self.assertEqual(states[-1]["last_action"], "stop")
        self.assertTrue(session["summary"]["target_seen"])
        self.assertTrue(session["summary"]["lost_target"])
        self.assertIn("target was lost after acquisition", session["summary"]["highlights"])

    def test_mock_demo_error_scenario_enters_error_and_stops(self):
        from src.app.demo import run_demo_session

        session = run_demo_session(["--mode", "mock", "--scenario", "error", "--ticks", "3"], verbose=False)

        self.assertEqual(session["states"][-1]["state"], "error")
        self.assertEqual(session["states"][-1]["last_action"], "stop")
        self.assertEqual(session["summary"]["final_state"], "error")
        self.assertFalse(session["summary"]["healthy"])
        self.assertIn("session ended in error", session["summary"]["highlights"])


if __name__ == "__main__":
    unittest.main()
