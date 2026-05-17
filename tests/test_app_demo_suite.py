import unittest


class DemoSuiteTest(unittest.TestCase):
    def test_run_demo_suite_runs_all_mock_scenarios(self):
        from src.app.demo import run_demo_suite

        suite = run_demo_suite(verbose=False)

        self.assertEqual(set(suite["sessions"]), {"empty", "approach", "lost_target", "error"})
        self.assertEqual(suite["sessions"]["approach"]["report"]["outcome"], "success")
        self.assertEqual(suite["sessions"]["lost_target"]["report"]["outcome"], "lost_target")
        self.assertEqual(suite["sessions"]["error"]["report"]["outcome"], "error")
        self.assertEqual(suite["outcome_counts"], {
            "no_target": 1,
            "success": 1,
            "lost_target": 1,
            "error": 1,
        })

    def test_run_demo_suite_includes_memory_updates_for_actionable_scenarios(self):
        from src.app.demo import run_demo_suite

        suite = run_demo_suite(verbose=False)

        self.assertIsNone(suite["sessions"]["empty"].get("memory_update"))
        self.assertEqual(suite["sessions"]["approach"]["memory_update"]["arm"], "approach")
        self.assertEqual(suite["sessions"]["lost_target"]["memory_update"]["arm"], "track_target")
        self.assertEqual(suite["sessions"]["error"]["memory_update"]["arm"], "safe_stop")

    def test_cli_all_scenarios_runs_suite(self):
        from src.app.demo import run_demo_entry

        result = run_demo_entry(["--mode", "mock", "--scenario", "all", "--include-memory-update"], verbose=False)

        self.assertIn("sessions", result)
        self.assertEqual(result["sessions"]["approach"]["summary"]["final_state"], "at_stop_distance")

    def test_run_demo_rejects_all_scenario_with_clear_error(self):
        from src.app.demo import run_demo

        with self.assertRaises(ValueError):
            run_demo(["--mode", "mock", "--scenario", "all"], verbose=False)

    def test_run_product_demo_suite_returns_artifacts_and_dashboard_preview(self):
        from src.app.demo import run_product_demo_suite

        result = run_product_demo_suite(verbose=False)

        self.assertEqual(set(result["artifacts"]), {"empty", "approach", "lost_target", "error"})
        self.assertEqual(result["dashboard_preview"]["total_sessions"], 4)
        self.assertTrue(result["dashboard_preview"]["milestone"]["complete"])
        self.assertEqual(result["dashboard_preview"]["outcome_counts"], {
            "no_target": 1,
            "success": 1,
            "lost_target": 1,
            "error": 1,
        })

    def test_run_product_demo_suite_can_persist_artifacts_when_store_provided(self):
        from src.app.demo import run_product_demo_suite

        store = FakeStore()

        result = run_product_demo_suite(verbose=False, store=store)

        self.assertEqual(len(store.saved), 4)
        self.assertEqual(set(result["artifacts"]), {artifact["scenario"] for artifact in store.saved})


class FakeStore:
    def __init__(self):
        self.saved = []

    def save(self, artifact):
        self.saved.append(artifact)
        return artifact


if __name__ == "__main__":
    unittest.main()
