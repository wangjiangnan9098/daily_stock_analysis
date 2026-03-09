# -*- coding: utf-8 -*-
import subprocess
import unittest
from unittest.mock import patch

from src.llm.browser_cli_adapter import BrowserCliAdapter


class TestBrowserCliCommandResolution(unittest.TestCase):
    @staticmethod
    def _shell_which_codex(args, **kwargs):
        if len(args) >= 3 and args[1] == "-lc" and "command -v codex" in args[2]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="/opt/homebrew/bin/codex\n", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")

    def test_resolve_codex_from_login_shell(self):
        def _isfile(path):
            return path == "/opt/homebrew/bin/codex"

        def _isexec(path, mode):
            return path == "/opt/homebrew/bin/codex"

        with patch("src.llm.browser_cli_adapter.shutil.which") as mock_which, \
                patch("src.llm.browser_cli_adapter.os.path.isfile", side_effect=_isfile), \
                patch("src.llm.browser_cli_adapter.os.access", side_effect=_isexec), \
                patch("src.llm.browser_cli_adapter.subprocess.run", side_effect=self._shell_which_codex):
            mock_which.side_effect = lambda cmd: "/bin/zsh" if cmd == "zsh" else None
            adapter = BrowserCliAdapter(
                command="codex",
                model_name="gpt-4o-mini",
                login_command="codex login",
                auth_check_command="codex login status",
            )

            self.assertTrue(adapter.is_available())
            self.assertEqual(adapter._base_cmd[0], "/opt/homebrew/bin/codex")
            self.assertEqual(adapter._login_cmd[0], "/opt/homebrew/bin/codex")
            self.assertEqual(adapter._auth_check_cmd[0], "/opt/homebrew/bin/codex")


if __name__ == "__main__":
    unittest.main()
