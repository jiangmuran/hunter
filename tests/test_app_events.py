import unittest


class AppEventTest(unittest.TestCase):
    def test_event_serializes_kind_tick_message_and_payload(self):
        from src.app.events import AppEvent, EventKind

        event = AppEvent(
            kind=EventKind.COMMAND,
            tick=7,
            message="sent rotate",
            payload={"action": "rotate_cw"},
        )

        data = event.to_dict()
        self.assertEqual(data["kind"], "command")
        self.assertEqual(data["tick"], 7)
        self.assertEqual(data["message"], "sent rotate")
        self.assertEqual(data["payload"], {"action": "rotate_cw"})
        self.assertIn("ts", data)


if __name__ == "__main__":
    unittest.main()
