import unittest


class WebUITest(unittest.TestCase):
    def test_build_web_ui_model_exposes_product_sections(self):
        from src.app.web_ui import build_web_ui_model

        model = build_web_ui_model()

        self.assertEqual(model["title"], "Hunter Software MVP")
        self.assertTrue(model["acceptance"]["ready_for_hardware_integration"])
        self.assertEqual(model["dashboard"]["total_sessions"], 4)
        self.assertIn("daily_diary", model)
        self.assertIn("personalization", model)

    def test_build_web_ui_model_attaches_dashboard_highlights_to_sessions(self):
        from src.app.web_ui import build_web_ui_model

        model = build_web_ui_model()

        self.assertIn("highlights", model["dashboard"])
        self.assertGreater(len(model["dashboard"]["highlights"]), 0)
        self.assertEqual(len(model["sessions"]), model["dashboard"]["total_sessions"])
        for session in model["sessions"]:
            self.assertIn("dashboard_highlights", session)
            self.assertEqual(session["dashboard_highlights"], model["dashboard"]["highlights"])

    def test_render_web_ui_html_contains_dashboard_diary_and_personalization(self):
        from src.app.web_ui import build_web_ui_model, render_web_ui_html

        html = render_web_ui_html(build_web_ui_model())

        self.assertIn("Hunter Software MVP", html)
        self.assertIn("Dashboard", html)
        self.assertIn("Daily Diary", html)
        self.assertIn("Personalization", html)
        self.assertIn("Ready for hardware integration", html)

    def test_render_web_ui_html_contains_interactive_console_sections(self):
        from src.app.web_ui import build_web_ui_model, render_web_ui_html

        html = render_web_ui_html(build_web_ui_model())

        self.assertIn("Scenario Console", html)
        self.assertIn("State Timeline", html)
        self.assertIn("Trajectory", html)
        self.assertIn("Highlights", html)
        self.assertIn("scenario-button is-active", html)
        self.assertIn("querySelectorAll('[data-scenario-button]')", html)

    def test_run_web_ui_preview_returns_html(self):
        from src.app.web_ui import run_web_ui_preview

        html = run_web_ui_preview(verbose=False)

        self.assertIn("<html", html)
        self.assertIn("Software MVP", html)


if __name__ == "__main__":
    unittest.main()
