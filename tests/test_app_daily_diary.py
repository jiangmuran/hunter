import unittest


class DailyDiaryTest(unittest.TestCase):
    def test_aggregate_daily_sessions_handles_empty_history(self):
        from src.app.daily_diary import aggregate_daily_sessions

        stats = aggregate_daily_sessions([], target_date="2026-05-16")

        self.assertEqual(stats["date"], "2026-05-16")
        self.assertEqual(stats["total_sessions"], 0)
        self.assertEqual(stats["outcome_counts"], {})
        self.assertEqual(stats["command_totals"], {})
        self.assertEqual(stats["highlights"], [])

    def test_aggregate_daily_sessions_counts_mixed_outcomes(self):
        from src.app.daily_diary import aggregate_daily_sessions

        stats = aggregate_daily_sessions([
            _artifact("s1", "success", ended_at="2026-05-16T10:00:00Z"),
            _artifact("s2", "lost_target", ended_at="2026-05-16T11:00:00Z"),
            _artifact("s3", "success", ended_at="2026-05-15T10:00:00Z"),
        ], target_date="2026-05-16")

        self.assertEqual(stats["total_sessions"], 2)
        self.assertEqual(stats["outcome_counts"], {"success": 1, "lost_target": 1})

    def test_aggregate_daily_sessions_merges_commands_and_highlights(self):
        from src.app.daily_diary import aggregate_daily_sessions

        stats = aggregate_daily_sessions([
            _artifact("s1", "success", {"forward": 2}, ["target acquired"], ended_at="2026-05-16T10:00:00Z"),
            _artifact("s2", "error", {"stop": 1, "forward": 1}, ["session ended in error"], ended_at="2026-05-16T11:00:00Z"),
        ], target_date="2026-05-16")

        self.assertEqual(stats["command_totals"], {"forward": 3, "stop": 1})
        self.assertEqual(stats["highlights"], ["target acquired", "session ended in error"])

    def test_build_daily_diary_uses_template_without_llm(self):
        from src.app.daily_diary import build_daily_diary

        diary = build_daily_diary({
            "date": "2026-05-16",
            "total_sessions": 2,
            "outcome_counts": {"success": 1, "lost_target": 1},
            "command_totals": {"forward": 3, "stop": 1},
            "highlights": ["target acquired"],
        })

        self.assertEqual(diary["mode"], "template")
        self.assertIn("2026-05-16", diary["text"])
        self.assertIn("2 次", diary["text"])
        self.assertIn("success: 1", diary["text"])
        self.assertIn("forward: 3", diary["text"])

    def test_template_handles_no_activity_day(self):
        from src.app.daily_diary import build_daily_diary

        diary = build_daily_diary({
            "date": "2026-05-16",
            "total_sessions": 0,
            "outcome_counts": {},
            "command_totals": {},
            "highlights": [],
        })

        self.assertIn("今天还没有互动记录", diary["text"])

    def test_build_daily_diary_calls_llm_fn_with_prompt(self):
        from src.app.daily_diary import build_daily_diary

        prompts = []

        def llm_fn(prompt):
            prompts.append(prompt)
            return "猫咪日报"

        diary = build_daily_diary({
            "date": "2026-05-16",
            "total_sessions": 1,
            "outcome_counts": {"success": 1},
            "command_totals": {"forward": 1},
            "highlights": [],
        }, llm_fn=llm_fn)

        self.assertEqual(diary["mode"], "llm")
        self.assertEqual(diary["text"], "猫咪日报")
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0], diary["prompt"])

    def test_build_daily_diary_prompt_is_fact_based(self):
        from src.app.daily_diary import build_daily_diary_prompt

        prompt = build_daily_diary_prompt({
            "date": "2026-05-16",
            "total_sessions": 1,
            "outcome_counts": {"error": 1},
            "command_totals": {"stop": 1},
            "highlights": ["session ended in error"],
        })

        self.assertIn("只能基于以下事实", prompt)
        self.assertIn("error: 1", prompt)
        self.assertIn("stop: 1", prompt)
        self.assertIn("session ended in error", prompt)

    def test_build_daily_diary_from_sessions_returns_stats_and_text(self):
        from src.app.daily_diary import build_daily_diary_from_sessions

        diary = build_daily_diary_from_sessions([
            _artifact("s1", "success", ended_at="2026-05-16T10:00:00Z"),
        ], target_date="2026-05-16")

        self.assertEqual(diary["stats"]["total_sessions"], 1)
        self.assertIn("text", diary)

    def test_daily_diary_prompt_includes_story_highlights(self):
        from src.app.daily_diary import aggregate_daily_sessions, build_daily_diary_prompt

        artifact = {
            "ended_at": "2026-05-28T12:00:00Z",
            "summary": {"command_counts": {"forward": 1}, "highlights": ["target acquired during session"]},
            "report": {"outcome": "success"},
            "highlight": {"story": "Hunter 安全靠近目标", "detail": "4 ticks"},
        }

        stats = aggregate_daily_sessions([artifact], target_date="2026-05-28")
        prompt = build_daily_diary_prompt(stats)

        self.assertIn("故事素材", prompt)
        self.assertIn("Hunter 安全靠近目标", prompt)


def _artifact(session_id, outcome, command_counts=None, highlights=None, ended_at="2026-05-16T10:00:00Z"):
    return {
        "id": session_id,
        "ended_at": ended_at,
        "summary": {
            "command_counts": command_counts or {},
            "highlights": highlights or [],
        },
        "report": {"outcome": outcome},
    }


if __name__ == "__main__":
    unittest.main()
