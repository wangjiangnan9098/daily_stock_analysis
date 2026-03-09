# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path

from src.config import Config, get_config


class TestConfigDatabasePath(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.env_path.write_text("STOCK_LIST=600519\nDATABASE_PATH=\n", encoding="utf-8")
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

    def test_empty_database_path_falls_back_to_default(self) -> None:
        cfg = get_config()
        self.assertEqual(cfg.database_path, "./data/stock_analysis.db")
        db_url = cfg.get_db_url()
        self.assertIn("stock_analysis.db", db_url)


if __name__ == "__main__":
    unittest.main()
