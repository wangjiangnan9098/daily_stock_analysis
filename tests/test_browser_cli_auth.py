# -*- coding: utf-8 -*-
"""Tests for browser CLI based LLM authentication."""

import os
import sys
import tempfile
import types
import unittest
import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch

if "json_repair" not in sys.modules:
    json_repair_stub = types.ModuleType("json_repair")
    json_repair_stub.repair_json = lambda text, *args, **kwargs: text
    sys.modules["json_repair"] = json_repair_stub

from src.analyzer import GeminiAnalyzer
from src.config import Config

_IMAGE_EXTRACTOR_PATH = Path(__file__).resolve().parents[1] / "src" / "services" / "image_stock_extractor.py"
_IMAGE_EXTRACTOR_SPEC = importlib.util.spec_from_file_location("image_stock_extractor_test_module", _IMAGE_EXTRACTOR_PATH)
_IMAGE_EXTRACTOR_MODULE = importlib.util.module_from_spec(_IMAGE_EXTRACTOR_SPEC)
assert _IMAGE_EXTRACTOR_SPEC and _IMAGE_EXTRACTOR_SPEC.loader
_IMAGE_EXTRACTOR_SPEC.loader.exec_module(_IMAGE_EXTRACTOR_MODULE)


class BrowserCliAuthTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.env_path.write_text("STOCK_LIST=600519\n", encoding="utf-8")
        os.environ["ENV_FILE"] = str(self.env_path)
        Config.reset_instance()

    def tearDown(self) -> None:
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        self.temp_dir.cleanup()

    def test_analyzer_uses_browser_cli_when_available(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LLM_AUTH_MODE": "browser_cli",
                "LLM_BROWSER_CLI_COMMAND": "openai",
                "LLM_BROWSER_CLI_MODEL": "gpt-4o-mini",
                "LLM_BROWSER_CLI_ALLOW_APIKEY_FALLBACK": "false",
            },
            clear=False,
        ):
            Config.reset_instance()
            with patch("src.analyzer.BrowserCliAdapter") as mock_adapter_cls:
                adapter = mock_adapter_cls.return_value
                adapter.is_available.return_value = True
                adapter.generate_text.return_value = "ok-from-browser-cli"

                analyzer = GeminiAnalyzer()
                output = analyzer._call_api_with_retry(
                    prompt="ping",
                    generation_config={"temperature": 0.1, "max_output_tokens": 128},
                )

                self.assertTrue(analyzer.is_available())
                self.assertEqual(output, "ok-from-browser-cli")
                adapter.generate_text.assert_called_once()

    def test_analyzer_unavailable_when_cli_missing_and_fallback_disabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LLM_AUTH_MODE": "browser_cli",
                "LLM_BROWSER_CLI_COMMAND": "missing-openai-cli",
                "LLM_BROWSER_CLI_ALLOW_APIKEY_FALLBACK": "false",
                "GEMINI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            Config.reset_instance()
            with patch("src.analyzer.BrowserCliAdapter") as mock_adapter_cls:
                adapter = mock_adapter_cls.return_value
                adapter.is_available.return_value = False

                analyzer = GeminiAnalyzer()
                self.assertFalse(analyzer.is_available())

    def test_image_extractor_uses_browser_cli_provider(self) -> None:
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        with patch.dict(
            os.environ,
            {
                "LLM_AUTH_MODE": "browser_cli",
                "LLM_BROWSER_CLI_COMMAND": "openai",
                "LLM_BROWSER_CLI_MODEL": "gpt-4o-mini",
                "LLM_BROWSER_CLI_ALLOW_APIKEY_FALLBACK": "false",
                "GEMINI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            Config.reset_instance()
            with patch.object(_IMAGE_EXTRACTOR_MODULE, "_build_browser_cli_adapter") as mock_builder:
                adapter = Mock()
                adapter.generate_vision.return_value = '["600519", "AAPL"]'
                mock_builder.return_value = adapter

                codes, raw_text = _IMAGE_EXTRACTOR_MODULE.extract_stock_codes_from_image(
                    png_bytes, "image/png"
                )

                self.assertEqual(codes, ["600519", "AAPL"])
                self.assertEqual(raw_text, '["600519", "AAPL"]')
                adapter.generate_vision.assert_called_once()


if __name__ == "__main__":
    unittest.main()
