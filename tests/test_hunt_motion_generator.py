import unittest


class MotionGeneratorTest(unittest.TestCase):
    def test_right_side_target_rotates_clockwise(self):
        from src.app.state import AppState, RuntimeState
        from src.hunt.motion_generator import MotionGenerator

        decision = MotionGenerator().decide(
            AppState(current=RuntimeState.ALIGNING),
            {"center_offset_x": 0.3, "size_ratio": 0.1},
        )

        self.assertEqual(decision.action, "rotate_cw")

    def test_left_side_target_rotates_counter_clockwise(self):
        from src.app.state import AppState, RuntimeState
        from src.hunt.motion_generator import MotionGenerator

        decision = MotionGenerator().decide(
            AppState(current=RuntimeState.ALIGNING),
            {"center_offset_x": -0.3, "size_ratio": 0.1},
        )

        self.assertEqual(decision.action, "rotate_ccw")

    def test_centered_far_target_moves_forward(self):
        from src.app.state import AppState, RuntimeState
        from src.hunt.motion_generator import MotionGenerator

        decision = MotionGenerator().decide(
            AppState(current=RuntimeState.APPROACHING),
            {"center_offset_x": 0.0, "size_ratio": 0.1},
        )

        self.assertEqual(decision.action, "forward")

    def test_near_target_stops(self):
        from src.app.state import AppState, RuntimeState
        from src.hunt.motion_generator import MotionGenerator

        decision = MotionGenerator().decide(
            AppState(current=RuntimeState.AT_STOP_DISTANCE),
            {"center_offset_x": 0.0, "size_ratio": 0.5},
        )

        self.assertEqual(decision.action, "stop")

    def test_error_and_lost_states_stop_safely(self):
        from src.app.state import AppState, RuntimeState
        from src.hunt.motion_generator import MotionGenerator

        generator = MotionGenerator()
        for runtime_state in [RuntimeState.ERROR, RuntimeState.EMERGENCY_STOP, RuntimeState.LOST_TARGET]:
            decision = generator.decide(AppState(current=runtime_state), None)
            self.assertEqual(decision.action, "stop")


if __name__ == "__main__":
    unittest.main()
