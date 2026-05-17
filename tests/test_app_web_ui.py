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

    def test_render_web_ui_html_contains_dashboard_diary_and_personalization(self):
        from src.app.web_ui import build_web_ui_model, render_web_ui_html

        html = render_web_ui_html(build_web_ui_model())

        self.assertIn("Hunter Software MVP", html)
        self.assertIn("Dashboard", html)
        self.assertIn("Daily Diary", html)
        self.assertIn("Personalization", html)
        self.assertIn("Ready for hardware integration", html)

    def test_run_web_ui_preview_returns_html(self):
        from src.app.web_ui import run_web_ui_preview

        html = run_web_ui_preview(verbose=False)

        self.assertIn("<html", html)
        self.assertIn("Software MVP", html)


if __name__ == "__main__":
    unittest.main()
