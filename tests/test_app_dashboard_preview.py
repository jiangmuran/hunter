import unittest


class DashboardPreviewTest(unittest.TestCase):
    def test_dashboard_preview_summarizes_history(self):
        from src.app.dashboard_preview import build_dashboard_preview

        preview = build_dashboard_preview([
            _artifact("s1", "success", {"forward": 1, "stop": 1}),
            _artifact("s2", "lost_target", {"stop": 2}),
        ])

        self.assertEqual(preview["title"], "Hunter 软件 MVP 仪表盘预览")
        self.assertEqual(preview["total_sessions"], 2)
        self.assertEqual(preview["outcome_counts"], {"success": 1, "lost_target": 1})
        self.assertEqual(preview["command_totals"], {"forward": 1, "stop": 3})

    def test_dashboard_preview_includes_latest_report(self):
        from src.app.dashboard_preview import build_dashboard_preview

        preview = build_dashboard_preview([
            _artifact("old", "no_target", {}, title="旧报告", ended_at="2026-05-16T10:00:00Z"),
            _artifact("new", "success", {}, title="新报告", ended_at="2026-05-16T10:01:00Z"),
        ])

        self.assertEqual(preview["latest_session"], {
            "id": "new",
            "scenario": "new",
            "outcome": "success",
            "title": "新报告",
        })
        self.assertEqual([session["id"] for session in preview["recent_sessions"]], ["new", "old"])

    def test_dashboard_preview_includes_milestone_when_suite_complete(self):
        from src.app.dashboard_preview import build_dashboard_preview

        milestone = {"name": "no_hardware_mvp", "complete": True}

        preview = build_dashboard_preview([], milestone=milestone)

        self.assertEqual(preview["milestone"], milestone)

    def test_dashboard_preview_includes_memory_preferences(self):
        from src.app.dashboard_preview import build_dashboard_preview

        preferences = [{"arm": "laser_escape", "expected_reward": 0.75}]

        preview = build_dashboard_preview([], memory_preferences=preferences)

        self.assertEqual(preview["memory_preferences"], preferences)


def _artifact(session_id, outcome, command_counts, title=None, ended_at="2026-05-16T10:00:00Z"):
    return {
        "id": session_id,
        "scenario": session_id,
        "ended_at": ended_at,
        "summary": {"command_counts": command_counts},
        "report": {"outcome": outcome, "title": title or outcome},
    }


if __name__ == "__main__":
    unittest.main()
