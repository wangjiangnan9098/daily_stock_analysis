# -*- coding: utf-8 -*-
import logging
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.logging_config import setup_logging


class TestLoggingConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.prev_cwd = os.getcwd()
        os.chdir(self.temp_dir.name)

    def tearDown(self) -> None:
        root = logging.getLogger()
        if root.handlers:
            root.handlers.clear()
        os.chdir(self.prev_cwd)
        self.temp_dir.cleanup()

    def test_relocate_cwd_logs_to_log_dir_with_original_name(self) -> None:
        today = datetime.now().strftime("%Y%m%d")
        normal_name = f"stock_analysis_{today}.log"
        debug_name = f"stock_analysis_debug_{today}.log"

        Path(normal_name).write_text("normal-old\n", encoding="utf-8")
        Path(debug_name).write_text("debug-old\n", encoding="utf-8")

        setup_logging(log_prefix="stock_analysis", log_dir="./logs", debug=False)
        logging.getLogger(__name__).info("test-line")

        normal_target = Path("logs") / normal_name
        debug_target = Path("logs") / debug_name

        self.assertTrue(normal_target.exists())
        self.assertTrue(debug_target.exists())
        self.assertFalse(Path(normal_name).exists())
        self.assertFalse(Path(debug_name).exists())


if __name__ == "__main__":
    unittest.main()
