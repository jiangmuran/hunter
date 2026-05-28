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


if __name__ == "__main__":
    unittest.main()
