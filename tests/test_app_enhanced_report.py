import unittest


class EnhancedReportTest(unittest.TestCase):
    def test_enhanced_report_combines_report_strategy_profile_and_plan(self):
        from src.app.enhanced_report import build_enhanced_report

        report = build_enhanced_report(
            {"title": "看到了猫", "text": "基础报告"},
            {"decision": "continue_engagement", "reason": "互动质量较高。", "next_action": "继续观察。"},
            {"play_style": "主动追逐型", "summary": "参与度高。"},
            {"recommended_arm": "laser_escape", "intensity": "medium", "operator_note": "保持节奏。"},
        )

        self.assertEqual(report["title"], "Hunter 软件智能报告")
        self.assertEqual(len(report["sections"]), 4)
        self.assertIn("基础报告", report["text"])
        self.assertIn("continue_engagement", report["text"])
        self.assertIn("主动追逐型", report["text"])
        self.assertIn("laser_escape", report["text"])


if __name__ == "__main__":
    unittest.main()
