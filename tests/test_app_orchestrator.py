import unittest


class StaticTracker:
    def __init__(self, target):
        self.target = target

    def update(self, detections):
        return self.target


class StaticDetector:
    def __init__(self, detections):
        self.detections = detections

    def detect(self, frame):
        return self.detections


class RaisingDetector:
    def detect(self, frame):
        raise RuntimeError("detector failed")


class OrchestratorTest(unittest.TestCase):
    def test_healthy_no_target_scans_and_stops(self):
        from src.app.mock_api import MockHunterAPI
        from src.app.orchestrator import AppOrchestrator
        from src.app.state import RuntimeState

        api = MockHunterAPI()
        orchestrator = AppOrchestrator(api=api, detector=StaticDetector([]), tracker=StaticTracker(None))

        state = orchestrator.tick()

        self.assertEqual(state.current, RuntimeState.SCANNING)
        self.assertEqual(api.command_history[-1]["action"], "stop")

    def test_off_center_target_dispatches_rotate(self):
        from src.app.mock_api import MockHunterAPI
        from src.app.orchestrator import AppOrchestrator

        api = MockHunterAPI()
        target = {"center_offset_x": 0.3, "size_ratio": 0.1, "missing_count": 0}
        orchestrator = AppOrchestrator(api=api, detector=StaticDetector([target]), tracker=StaticTracker(target))

        state = orchestrator.tick()

        self.assertEqual(state.last_action, "rotate_cw")
        self.assertEqual(api.command_history[-1]["action"], "rotate_cw")

    def test_centered_far_target_dispatches_forward(self):
        from src.app.mock_api import MockHunterAPI
        from src.app.orchestrator import AppOrchestrator

        api = MockHunterAPI()
        target = {"center_offset_x": 0.0, "size_ratio": 0.1, "missing_count": 0}
        orchestrator = AppOrchestrator(api=api, detector=StaticDetector([target]), tracker=StaticTracker(target))

        state = orchestrator.tick()

        self.assertEqual(state.last_action, "forward")
        self.assertEqual(api.command_history[-1]["action"], "forward")

    def test_near_target_dispatches_stop(self):
        from src.app.mock_api import MockHunterAPI
        from src.app.orchestrator import AppOrchestrator

        api = MockHunterAPI()
        target = {"center_offset_x": 0.0, "size_ratio": 0.5, "missing_count": 0}
        orchestrator = AppOrchestrator(api=api, detector=StaticDetector([target]), tracker=StaticTracker(target))

        state = orchestrator.tick()

        self.assertEqual(state.last_action, "stop")
        self.assertEqual(api.command_history[-1]["action"], "stop")

    def test_tracker_exception_goes_error_and_stops(self):
        from src.app.mock_api import MockHunterAPI
        from src.app.orchestrator import AppOrchestrator
        from src.app.state import RuntimeState

        api = MockHunterAPI()
        orchestrator = AppOrchestrator(api=api, detector=RaisingDetector(), tracker=StaticTracker(None))

        state = orchestrator.tick()

        self.assertEqual(state.current, RuntimeState.ERROR)
        self.assertEqual(api.command_history[-1]["action"], "stop")
        self.assertIn("detector failed", state.error)

    def test_events_are_appended_for_state_and_command(self):
        from src.app.mock_api import MockHunterAPI
        from src.app.orchestrator import AppOrchestrator

        api = MockHunterAPI()
        target = {"center_offset_x": -0.3, "size_ratio": 0.1, "missing_count": 0}
        orchestrator = AppOrchestrator(api=api, detector=StaticDetector([target]), tracker=StaticTracker(target))

        orchestrator.tick()

        event_kinds = [event.kind.value for event in orchestrator.events]
        self.assertIn("state", event_kinds)
        self.assertIn("command", event_kinds)


if __name__ == "__main__":
    unittest.main()
