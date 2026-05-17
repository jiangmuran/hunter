import os
import unittest
from unittest.mock import patch


class LLMConfigTest(unittest.TestCase):
    def test_default_llm_config_is_disabled(self):
        from src.app.llm_config import load_llm_config

        with patch.dict(os.environ, {}, clear=True):
            config = load_llm_config()

        self.assertFalse(config["enabled"])
        self.assertEqual(config["provider"], "none")
        self.assertIsNone(config["api_key_env"])

    def test_load_llm_config_reads_provider_model_and_key_env_name(self):
        from src.app.llm_config import load_llm_config

        with patch.dict(os.environ, {
            "HUNTER_LLM_PROVIDER": "anthropic",
            "HUNTER_LLM_MODEL": "claude-sonnet-4-6",
            "HUNTER_LLM_API_KEY_ENV": "ANTHROPIC_API_KEY",
            "ANTHROPIC_API_KEY": "secret",
        }, clear=True):
            config = load_llm_config()

        self.assertTrue(config["enabled"])
        self.assertEqual(config["provider"], "anthropic")
        self.assertEqual(config["model"], "claude-sonnet-4-6")
        self.assertEqual(config["api_key_env"], "ANTHROPIC_API_KEY")
        self.assertTrue(config["api_key_configured"])

    def test_describe_llm_config_explains_where_to_configure(self):
        from src.app.llm_config import describe_llm_config

        text = describe_llm_config({
            "enabled": False,
            "provider": "none",
            "model": None,
            "api_key_env": None,
            "api_key_configured": False,
        })

        self.assertIn("HUNTER_LLM_PROVIDER", text)
        self.assertIn("HUNTER_LLM_MODEL", text)
        self.assertIn("HUNTER_LLM_API_KEY_ENV", text)


if __name__ == "__main__":
    unittest.main()
