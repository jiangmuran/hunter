import unittest


class SessionHighlightsTest(unittest.TestCase):
    def test_build_session_highlights_describes_success(self):
        from src.app.session_highlights import build_session_highlights

        artifact = {
            "scenario": "approach",
            "summary": {
                "ticks": 4,
                "reached_stop_distance": True,
                "lost_target": False,
                "activity": {"engagement_score": 75},
                "trajectory": {"point_count": 3, "path_length": 124.2},
            },
            "report": {"outcome": "success", "title": "看到了猫，并安全靠近到制动距离"},
        }

        highlights = build_session_highlights([artifact])

        self.assertEqual(highlights[0]["scenario"], "approach")
        self.assertEqual(highlights[0]["tone"], "success")
        self.assertIn("安全靠近", highlights[0]["story"])
        self.assertIn("124.2", highlights[0]["detail"])

    def test_build_session_highlights_handles_error(self):
        from src.app.session_highlights import build_session_highlights

        artifact = {
            "scenario": "error",
            "summary": {"ticks": 3, "error": "mock detector failed", "activity": {"engagement_score": 0}, "trajectory": {"point_count": 0, "path_length": 0}},
            "report": {"outcome": "error", "title": "发生异常，已停车"},
        }

        highlights = build_session_highlights([artifact])

        self.assertEqual(highlights[0]["tone"], "danger")
        self.assertIn("异常", highlights[0]["story"])

    def test_lost_target_outcome(self):
        from src.app.session_highlights import build_session_highlights

        artifact = {
            "scenario": "chase",
            "summary": {
                "ticks": 6,
                "lost_target": True,
                "activity": {"engagement_score": 40},
                "trajectory": {"point_count": 5, "path_length": 300.0},
            },
            "report": {"outcome": "lost_target", "title": "目标消失，Hunter 停车等待"},
        }

        highlights = build_session_highlights([artifact])

        self.assertEqual(highlights[0]["tone"], "warning")
        self.assertIn("消失", highlights[0]["story"])
        self.assertEqual(highlights[0]["outcome"], "lost_target")

    def test_no_target_outcome(self):
        from src.app.session_highlights import build_session_highlights

        artifact = {
            "scenario": "idle",
            "summary": {
                "ticks": 2,
                "activity": {"engagement_score": 0},
                "trajectory": {"point_count": 0, "path_length": 0},
            },
            "report": {"outcome": "no_target", "title": "未检测到猫"},
        }

        highlights = build_session_highlights([artifact])

        self.assertEqual(highlights[0]["tone"], "calm")
        self.assertIn("未检测到目标", highlights[0]["story"])
        self.assertEqual(highlights[0]["outcome"], "no_target")

    def test_unknown_outcome_fallback(self):
        from src.app.session_highlights import build_session_highlights

        artifact = {
            "scenario": "play",
            "summary": {
                "ticks": 1,
                "activity": {"engagement_score": 20},
                "trajectory": {"point_count": 1, "path_length": 10.0},
            },
            "report": {"outcome": "curious_approach", "title": "猫好奇靠近"},
        }

        highlights = build_session_highlights([artifact])

        self.assertEqual(highlights[0]["tone"], "calm")
        self.assertIn("完成了一次交互记录", highlights[0]["story"])
        self.assertEqual(highlights[0]["outcome"], "curious_approach")

    def test_sparse_empty_artifact(self):
        from src.app.session_highlights import build_session_highlights

        highlights = build_session_highlights([{}])

        card = highlights[0]
        self.assertEqual(card["scenario"], "")
        self.assertEqual(card["outcome"], "unknown")
        self.assertEqual(card["tone"], "calm")
        self.assertEqual(card["title"], "未命名互动")
        self.assertIn("ticks=0", card["detail"])
        self.assertIn("trajectory_points=0", card["detail"])
        self.assertIn("path_length=0", card["detail"])
        self.assertIn("engagement_score=0", card["detail"])

    def test_sparse_none_summary_and_report(self):
        from src.app.session_highlights import build_session_highlights

        artifact = {"summary": None, "report": None}

        # This should not raise
        highlights = build_session_highlights([artifact])

        card = highlights[0]
        self.assertEqual(card["scenario"], "")
        self.assertEqual(card["outcome"], "unknown")
        self.assertEqual(card["tone"], "calm")
        self.assertEqual(card["title"], "未命名互动")
        self.assertIn("ticks=0", card["detail"])
        self.assertIn("trajectory_points=0", card["detail"])
        self.assertIn("path_length=0", card["detail"])
        self.assertIn("engagement_score=0", card["detail"])


if __name__ == "__main__":
    unittest.main()
