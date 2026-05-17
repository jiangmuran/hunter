import tempfile
import unittest
from pathlib import Path


class SessionStoreTest(unittest.TestCase):
    def test_store_saves_and_gets_session_artifact(self):
        from src.app.session_store import SessionStore

        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.jsonl")
            artifact = _artifact("s1", "success", {"forward": 1})

            saved = store.save(artifact)

            self.assertEqual(saved, artifact)
            self.assertEqual(store.get("s1"), artifact)

    def test_store_lists_recent_sessions_newest_first(self):
        from src.app.session_store import SessionStore

        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.jsonl")
            store.save(_artifact("old", "no_target", {}, ended_at="2026-05-16T10:00:00Z"))
            store.save(_artifact("new", "success", {}, ended_at="2026-05-16T10:01:00Z"))

            recent = store.list_recent(limit=2)

            self.assertEqual([session["id"] for session in recent], ["new", "old"])

    def test_store_overview_counts_outcomes_and_commands(self):
        from src.app.session_store import SessionStore

        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.jsonl")
            store.save(_artifact("s1", "success", {"forward": 1, "stop": 1}))
            store.save(_artifact("s2", "lost_target", {"stop": 2}))

            overview = store.overview()

            self.assertEqual(overview["total_sessions"], 2)
            self.assertEqual(overview["outcome_counts"], {"success": 1, "lost_target": 1})
            self.assertEqual(overview["command_totals"], {"forward": 1, "stop": 3})

    def test_store_handles_empty_history(self):
        from src.app.session_store import SessionStore

        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.jsonl")

            self.assertIsNone(store.get("missing"))
            self.assertEqual(store.list_recent(), [])
            self.assertEqual(store.overview(), {
                "total_sessions": 0,
                "outcome_counts": {},
                "command_totals": {},
            })


def _artifact(session_id, outcome, command_counts, ended_at="2026-05-16T10:00:00Z"):
    return {
        "id": session_id,
        "mode": "mock",
        "scenario": session_id,
        "ended_at": ended_at,
        "summary": {"command_counts": command_counts},
        "report": {"outcome": outcome, "title": outcome},
    }


if __name__ == "__main__":
    unittest.main()
