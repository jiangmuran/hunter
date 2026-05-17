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

    def test_run_product_demo_suite_preserves_history_across_runs(self):
        from src.app.demo import run_product_demo_suite

        store = FakeStore()

        first = run_product_demo_suite(verbose=False, store=store)
        second = run_product_demo_suite(verbose=False, store=store)

        first_ids = {artifact["id"] for artifact in first["artifacts"].values()}
        second_ids = {artifact["id"] for artifact in second["artifacts"].values()}
        self.assertEqual(len(store.saved), 8)
        self.assertTrue(first_ids.isdisjoint(second_ids))

    def test_run_product_demo_suite_includes_daily_diary_preview(self):
        from src.app.demo import run_product_demo_suite

        result = run_product_demo_suite(verbose=False)

        self.assertIn("daily_diary", result)
        self.assertEqual(result["daily_diary"]["stats"]["total_sessions"], 4)
        self.assertIn("text", result["daily_diary"])

    def test_run_product_demo_suite_applies_memory_updates_when_memory_box_provided(self):
        from src.app.demo import run_product_demo_suite

        memory_box = FakeMemoryBox()

        result = run_product_demo_suite(verbose=False, memory_box=memory_box)

        self.assertEqual(memory_box.updates, [
            ("laser_escape", 1),
            ("wand_hover", 0),
            ("wand_slow", 0),
        ])
        self.assertEqual(result["memory_updates"], [
            {"app_arm": "approach", "memory_arm": "laser_escape", "reward": 1, "reason": "reached_stop_distance"},
            {"app_arm": "track_target", "memory_arm": "wand_hover", "reward": 0, "reason": "lost_target"},
            {"app_arm": "safe_stop", "memory_arm": "wand_slow", "reward": 0, "reason": "error"},
        ])

    def test_run_software_mvp_acceptance_returns_ready_summary(self):
        from src.app.demo import run_software_mvp_acceptance

        result = run_software_mvp_acceptance(verbose=False)

        self.assertTrue(result["ready_for_hardware_integration"])
        self.assertEqual(result["total_sessions"], 4)
        self.assertIn("dashboard_preview", result["capabilities"])
        self.assertIn("daily_diary", result["capabilities"])
        self.assertIn("real robot closed loop", result["remaining_for_real_mvp"])
    def test_run_product_demo_suite_includes_personalization_preview(self):
        from src.app.demo import run_product_demo_suite

        memory_box = FakeMemoryBox()
        memory_box.preferences = [("laser_escape", 0.9)]

        result = run_product_demo_suite(verbose=False, memory_box=memory_box)

        self.assertEqual(result["personalization_preview"]["recommended_arm"], "laser_escape")
        self.assertEqual(result["personalization_preview"]["source"], "memory")
    def test_run_software_mvp_acceptance_includes_personalization_readiness(self):
        from src.app.demo import run_software_mvp_acceptance

        result = run_software_mvp_acceptance(verbose=False)

        self.assertEqual(result["personalization"]["recommended_arm"], "wand_slow")
        self.assertEqual(result["personalization"]["source"], "default")
        self.assertIn("personalization_policy", result["capabilities"])


class FakeStore:
    def __init__(self):
        self.saved = []

    def save(self, artifact):
        self.saved.append(artifact)
        return artifact


class FakeMemoryBox:
    def __init__(self):
        self.updates = []
        self.preferences = []

    def update(self, arm, reward):
        self.updates.append((arm, reward))

    def top_preferences(self, limit):
        if self.preferences:
            return self.preferences[:limit]
        return self.updates[:limit]


if __name__ == "__main__":
    unittest.main()
