import unittest


class MvpMilestoneTest(unittest.TestCase):
    def test_milestone_summarizes_suite_as_complete(self):
        from src.app.mvp_milestone import build_mvp_milestone

        milestone = build_mvp_milestone({
            "outcome_counts": {"no_target": 1, "success": 1, "lost_target": 1, "error": 1},
            "sessions": {
                "empty": {"report": {"outcome": "no_target"}},
                "approach": {"report": {"outcome": "success"}},
                "lost_target": {"report": {"outcome": "lost_target"}},
                "error": {"report": {"outcome": "error"}},
            },
        })

        self.assertTrue(milestone["complete"])
        self.assertEqual(milestone["name"], "no_hardware_mvp")
        self.assertIn("无硬件 MVP 已覆盖成功、空场、丢猫和异常四类核心路径", milestone["headline"])
        self.assertIn("mock scenario suite", milestone["completed_capabilities"])
        self.assertIn("real robot closed loop", milestone["next_phase"])

    def test_milestone_marks_missing_outcomes_incomplete(self):
        from src.app.mvp_milestone import build_mvp_milestone

        milestone = build_mvp_milestone({"outcome_counts": {"success": 1}, "sessions": {}})

        self.assertFalse(milestone["complete"])
        self.assertEqual(set(milestone["missing_outcomes"]), {"no_target", "lost_target", "error"})

    def test_run_demo_suite_includes_milestone(self):
        from src.app.demo import run_demo_suite

        suite = run_demo_suite(verbose=False)

        self.assertIn("milestone", suite)
        self.assertTrue(suite["milestone"]["complete"])


if __name__ == "__main__":
    unittest.main()
