# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path

from src.config import Config, get_config


class TestConfigBrowserCliBool(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.prev_env_file = os.environ.get("ENV_FILE")
        os.environ["ENV_FILE"] = str(self.env_path)
        Config.reset_instance()

    def tearDown(self) -> None:
        Config.reset_instance()
        if self.prev_env_file is None:
            os.environ.pop("ENV_FILE", None)
        else:
            os.environ["ENV_FILE"] = self.prev_env_file
        self.temp_dir.cleanup()

    def test_empty_auto_login_uses_default_true(self) -> None:
        self.env_path.write_text(
            "STOCK_LIST=600519\nLLM_AUTH_MODE=browser_cli\nLLM_BROWSER_CLI_AUTO_LOGIN=\n",
            encoding="utf-8",
        )
        cfg = get_config()
        self.assertTrue(cfg.llm_browser_cli_auto_login)

    def test_empty_allow_fallback_uses_default_false(self) -> None:
        Config.reset_instance()
        self.env_path.write_text(
            "STOCK_LIST=600519\nLLM_AUTH_MODE=browser_cli\nLLM_BROWSER_CLI_ALLOW_APIKEY_FALLBACK=\n",
            encoding="utf-8",
        )
        cfg = get_config()
        self.assertFalse(cfg.llm_browser_cli_allow_apikey_fallback)


if __name__ == "__main__":
    unittest.main()
