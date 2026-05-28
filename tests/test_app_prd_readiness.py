import unittest

from src.app.prd_readiness import build_onsite_demo_check, build_prd_software_coverage


class PrdReadinessTest(unittest.TestCase):
    def test_prd_coverage_names_real_product_gaps(self):
        coverage = build_prd_software_coverage()

        self.assertFalse(coverage["real_product_ready"])
        self.assertEqual(coverage["blockers"], [])
        self.assertTrue(coverage["software_demo_ready"])
        audio = _feature(coverage, "audio_emotion")
        treat = _feature(coverage, "treat_reward")
        self.assertEqual(audio["status"], "mock_usable")
        self.assertEqual(treat["status"], "mock_usable")
        self.assertIn("src/app/audio_emotion.py", audio["evidence"])
        self.assertIn("src/app/treat_reward.py", treat["evidence"])

    def test_prd_coverage_marks_remote_app_out_of_scope(self):
        coverage = build_prd_software_coverage()

        remote = _feature(coverage, "remote_app_control")
        self.assertEqual(remote["status"], "out_of_scope")
        self.assertIn("APP/WebUI", remote["real_use_gap"])

    def test_prd_coverage_includes_surprise_entropy_evidence(self):
        coverage = build_prd_software_coverage()

        entropy = _feature(coverage, "surprise_entropy")
        self.assertEqual(entropy["status"], "mock_usable")
        self.assertIn("src/app/surprise_entropy.py", entropy["evidence"])

    def test_onsite_check_reports_demo_commands_and_real_gaps(self):
        check = build_onsite_demo_check(
            product_suite={"outcome_counts": {"success": 1, "error": 1}},
            intelligence_brief={
                "capabilities": ["surprise_entropy_engine"],
                "strategy": {"decision": "safe_pause"},
                "enhanced_report": {"text": "看到了猫，并安全靠近到制动距离"},
                "surprise_entropy": {"selected_action": {"intensity": "low"}},
            },
            entropy_preview={"candidates": [{"novelty": 0.65}, {"novelty": 1.0}]},
        )

        self.assertFalse(check["ready"])
        self.assertTrue(check["software_abstraction_ready"])
        self.assertTrue(check["software_demo_ready"])
        self.assertFalse(check["real_product_ready"])
        self.assertIn("python -m src.app.demo --software-intelligence-brief", check["demo_commands"])
        self.assertIn("python -m src.app.demo --audio-emotion-preview", check["demo_commands"])
        self.assertIn("python -m src.app.demo --treat-reward-preview", check["demo_commands"])
        self.assertIn("真实可用产品", check["real_use_gap_summary"])

    def test_onsite_check_flags_consistency_failures(self):
        check = build_onsite_demo_check(
            product_suite={"outcome_counts": {"success": 0}},
            intelligence_brief={
                "capabilities": [],
                "strategy": {"decision": "safe_pause"},
                "enhanced_report": {"text": "session ended in error"},
                "surprise_entropy": {"selected_action": {"intensity": "high"}},
            },
            entropy_preview={"candidates": [{"novelty": 1.0}]},
        )

        failed = [item["name"] for item in check["consistency_checks"] if not item["passed"]]
        self.assertIn("safe strategy does not select high intensity action", failed)
        self.assertIn("intelligence brief has entropy engine", failed)


def _feature(coverage, feature_id):
    return next(feature for feature in coverage["features"] if feature["id"] == feature_id)


if __name__ == "__main__":
    unittest.main()
