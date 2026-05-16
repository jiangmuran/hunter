import unittest


class StateMachineTest(unittest.TestCase):
    def test_no_target_scans(self):
        from src.app.state import AppState, RuntimeState
        from src.app.state_machine import StateMachine

        next_state = StateMachine().transition(AppState(), target=None)

        self.assertEqual(next_state.current, RuntimeState.SCANNING)

    def test_off_center_target_aligns(self):
        from src.app.state import AppState, RuntimeState
        from src.app.state_machine import StateMachine

        target = {"center_offset_x": 0.3, "size_ratio": 0.1, "missing_count": 0}
        next_state = StateMachine().transition(AppState(), target=target)

        self.assertEqual(next_state.current, RuntimeState.ALIGNING)

    def test_centered_far_target_approaches(self):
        from src.app.state import AppState, RuntimeState
        from src.app.state_machine import StateMachine

        target = {"center_offset_x": 0.03, "size_ratio": 0.1, "missing_count": 0}
        next_state = StateMachine().transition(AppState(), target=target)

        self.assertEqual(next_state.current, RuntimeState.APPROACHING)

    def test_near_target_stops_at_distance(self):
        from src.app.state import AppState, RuntimeState
        from src.app.state_machine import StateMachine

        target = {"center_offset_x": 0.0, "size_ratio": 0.5, "missing_count": 0}
        next_state = StateMachine().transition(AppState(), target=target)

        self.assertEqual(next_state.current, RuntimeState.AT_STOP_DISTANCE)

    def test_repeated_misses_loses_target(self):
        from src.app.config import AppConfig
        from src.app.state import AppState, RuntimeState
        from src.app.state_machine import StateMachine

        machine = StateMachine(AppConfig(missing_limit=2))
        target = {"missing_count": 2}
        next_state = machine.transition(AppState(), target=target)

        self.assertEqual(next_state.current, RuntimeState.LOST_TARGET)

    def test_unhealthy_goes_error(self):
        from src.app.state import AppState, RuntimeState
        from src.app.state_machine import StateMachine

        next_state = StateMachine().transition(AppState(), target=None, healthy=False)

        self.assertEqual(next_state.current, RuntimeState.ERROR)
        self.assertFalse(next_state.healthy)

    def test_emergency_overrides_all(self):
        from src.app.state import AppState, RuntimeState
        from src.app.state_machine import StateMachine

        target = {"center_offset_x": 0.0, "size_ratio": 0.1, "missing_count": 0}
        next_state = StateMachine().transition(AppState(), target=target, emergency=True)

        self.assertEqual(next_state.current, RuntimeState.EMERGENCY_STOP)


if __name__ == "__main__":
    unittest.main()
