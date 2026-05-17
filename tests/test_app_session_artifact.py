import unittest


class SessionArtifactTest(unittest.TestCase):
    def test_build_session_artifact_wraps_session_with_metadata(self):
        from src.app.session_artifact import build_session_artifact

        session = {
            "states": [{"state": "scanning"}],
            "events": [{"kind": "command", "message": "stop"}],
            "summary": {"final_state": "scanning"},
            "report": {"outcome": "no_target", "title": "本次没有看到猫，保持安全待机"},
        }

        artifact = build_session_artifact(
            session,
            mode="mock",
            scenario="empty",
            session_id="demo-empty",
            started_at="2026-05-16T10:00:00Z",
            ended_at="2026-05-16T10:00:01Z",
        )

        self.assertEqual(artifact["id"], "demo-empty")
        self.assertEqual(artifact["mode"], "mock")
        self.assertEqual(artifact["scenario"], "empty")
        self.assertEqual(artifact["started_at"], "2026-05-16T10:00:00Z")
        self.assertEqual(artifact["ended_at"], "2026-05-16T10:00:01Z")
        self.assertEqual(artifact["states"], session["states"])
        self.assertEqual(artifact["events"], session["events"])
        self.assertEqual(artifact["summary"], session["summary"])
        self.assertEqual(artifact["report"], session["report"])

    def test_artifact_uses_stable_id_when_provided(self):
        from src.app.session_artifact import build_session_artifact

        artifact = build_session_artifact(
            {"states": [], "events": [], "summary": {}, "report": {}},
            mode="mock",
            scenario="approach",
            session_id="demo-1",
        )

        self.assertEqual(artifact["id"], "demo-1")

    def test_artifact_includes_memory_update_when_present(self):
        from src.app.session_artifact import build_session_artifact

        session = {
            "states": [],
            "events": [],
            "summary": {},
            "report": {},
            "memory_update": {"arm": "approach", "reward": 1, "reason": "reached_stop_distance"},
        }

        artifact = build_session_artifact(session, mode="mock", scenario="approach", session_id="demo-memory")

        self.assertEqual(artifact["memory_update"], session["memory_update"])


if __name__ == "__main__":
    unittest.main()
