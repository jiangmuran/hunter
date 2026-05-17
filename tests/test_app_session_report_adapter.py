import unittest


class SessionReportAdapterTest(unittest.TestCase):
    def test_log_session_report_records_success_as_play_win(self):
        from src.app.session_report_adapter import log_session_to_report

        logger = FakeLogger()
        summary = {"target_seen": True, "ticks": 4}
        report = {"outcome": "success"}

        result = log_session_to_report(summary, report, logger, arm="laser_escape")

        self.assertEqual(logger.activity_scores, [0.9])
        self.assertEqual(logger.play_events, [("laser_escape", 1, 0.4)])
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["reward"], 1)

    def test_log_session_report_records_lost_or_error_as_play_loss(self):
        from src.app.session_report_adapter import log_session_to_report

        logger = FakeLogger()
        summary = {"target_seen": True, "ticks": 7}
        report = {"outcome": "lost_target"}

        result = log_session_to_report(summary, report, logger, arm="wand_hover")

        self.assertEqual(logger.activity_scores, [0.5])
        self.assertEqual(logger.play_events, [("wand_hover", 0, 0.7)])
        self.assertEqual(result["reward"], 0)

    def test_log_session_report_records_no_target_as_low_activity_only(self):
        from src.app.session_report_adapter import log_session_to_report

        logger = FakeLogger()
        summary = {"target_seen": False, "ticks": 3}
        report = {"outcome": "no_target"}

        result = log_session_to_report(summary, report, logger)

        self.assertEqual(logger.activity_scores, [0.1])
        self.assertEqual(logger.play_events, [])
        self.assertEqual(result["play_logged"], False)


class FakeLogger:
    def __init__(self):
        self.activity_scores = []
        self.play_events = []

    def log_activity(self, score):
        self.activity_scores.append(score)

    def log_play(self, arm, reward, duration):
        self.play_events.append((arm, reward, duration))


if __name__ == "__main__":
    unittest.main()
