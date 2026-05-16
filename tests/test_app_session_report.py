import unittest


class SessionReportTest(unittest.TestCase):
    def test_report_marks_successful_approach(self):
        from src.app.session_report import build_session_report

        report = build_session_report({
            "ticks": 4,
            "final_state": "at_stop_distance",
            "healthy": True,
            "error": None,
            "target_seen": True,
            "lost_target": False,
            "reached_stop_distance": True,
            "last_action": "stop",
            "state_counts": {"scanning": 1, "aligning": 1, "approaching": 1, "at_stop_distance": 1},
            "command_counts": {"stop": 2, "rotate_cw": 1, "forward": 1},
            "highlights": ["target acquired during session", "approached target and stopped at safe distance"],
        })

        self.assertEqual(report["outcome"], "success")
        self.assertIn("看到了猫，并安全靠近到制动距离", report["title"])
        self.assertIn("forward × 1", report["command_line"])
        self.assertIn("rotate_cw × 1", report["command_line"])

    def test_report_marks_lost_target(self):
        from src.app.session_report import build_session_report

        report = build_session_report({
            "ticks": 7,
            "final_state": "lost_target",
            "healthy": True,
            "error": None,
            "target_seen": True,
            "lost_target": True,
            "reached_stop_distance": False,
            "last_action": "stop",
            "state_counts": {"approaching": 4, "lost_target": 3},
            "command_counts": {"forward": 4, "stop": 3},
            "highlights": ["target acquired during session", "target was lost after acquisition"],
        })

        self.assertEqual(report["outcome"], "lost_target")
        self.assertIn("中途丢失目标，已安全停车", report["title"])
        self.assertIn("最终状态：lost_target", report["lines"])

    def test_report_marks_error(self):
        from src.app.session_report import build_session_report

        report = build_session_report({
            "ticks": 3,
            "final_state": "error",
            "healthy": False,
            "error": "mock detector failed",
            "target_seen": False,
            "lost_target": False,
            "reached_stop_distance": False,
            "last_action": "stop",
            "state_counts": {"error": 3},
            "command_counts": {"stop": 3},
            "highlights": ["session ended in error"],
        })

        self.assertEqual(report["outcome"], "error")
        self.assertIn("发生异常，已停车", report["title"])
        self.assertIn("错误：mock detector failed", report["lines"])

    def test_demo_session_returns_report(self):
        from src.app.demo import run_demo_session

        session = run_demo_session(["--mode", "mock", "--scenario", "approach", "--ticks", "4"], verbose=False)

        self.assertIn("report", session)
        self.assertEqual(session["report"]["outcome"], "success")


if __name__ == "__main__":
    unittest.main()
