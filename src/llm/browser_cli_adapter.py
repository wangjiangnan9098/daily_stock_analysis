# -*- coding: utf-8 -*-
"""Browser-auth CLI adapter for GPT-first model invocation."""

from __future__ import annotations

import base64
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional


class BrowserCliError(RuntimeError):
    """Raised when browser CLI invocation fails."""


class BrowserCliAdapter:
    """Adapter for CLI-based LLM calls authenticated by browser login."""

    def __init__(
        self,
        command: str,
        model_name: str,
        timeout_sec: int = 120,
        login_command: str = "",
        auth_check_command: str = "",
        auto_login: bool = True,
        workdir: Optional[str] = None,
    ):
        self.command = (command or "").strip()
        self.model_name = (model_name or "").strip() or "gpt-4o-mini"
        self.timeout_sec = max(10, int(timeout_sec))
        self.login_command = (login_command or "").strip()
        self.auth_check_command = (auth_check_command or "").strip()
        self.auto_login = bool(auto_login)
        self.workdir = workdir
        self._base_cmd = self._split_command(self.command)
        self._login_cmd = self._split_command(self.login_command)
        self._auth_check_cmd = self._split_command(self.auth_check_command)
        self._resolve_command_paths()
        self._auth_checked = False
        self._authenticated = False

    @staticmethod
    def _split_command(command: str) -> List[str]:
        if not command:
            return []
        return shlex.split(command)

    def is_available(self) -> bool:
        if not self._base_cmd:
            return False
        exe = self._base_cmd[0]
        return self._is_executable(exe)

    @staticmethod
    def _is_executable(exe: str) -> bool:
        if not exe:
            return False
        if "/" in exe:
            return os.path.isfile(exe) and os.access(exe, os.X_OK)
        return bool(shutil.which(exe))

    @staticmethod
    def _replace_cmd_executable(cmd: List[str], resolved_exe: str) -> List[str]:
        if not cmd:
            return cmd
        updated = list(cmd)
        updated[0] = resolved_exe
        return updated

    def _resolve_command_paths(self) -> None:
        if not self._base_cmd:
            return
        original_exe = self._base_cmd[0]
        resolved_exe = self._resolve_exec_path(original_exe)
        if not resolved_exe:
            return
        self._base_cmd = self._replace_cmd_executable(self._base_cmd, resolved_exe)
        if self._login_cmd and self._login_cmd[0] == original_exe:
            self._login_cmd = self._replace_cmd_executable(self._login_cmd, resolved_exe)
        if self._auth_check_cmd and self._auth_check_cmd[0] == original_exe:
            self._auth_check_cmd = self._replace_cmd_executable(self._auth_check_cmd, resolved_exe)

    def _resolve_exec_path(self, exe: str) -> Optional[str]:
        if not exe:
            return None

        if self._is_executable(exe):
            return exe

        found = shutil.which(exe)
        if found:
            return found

        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, ".local", "bin", exe),
            os.path.join(home, "bin", exe),
            f"/opt/homebrew/bin/{exe}",
            f"/usr/local/bin/{exe}",
            f"/usr/bin/{exe}",
        ]
        for candidate in candidates:
            if self._is_executable(candidate):
                return candidate

        shell_path = self._resolve_exec_from_login_shell(exe)
        if shell_path:
            return shell_path
        return None

    def _resolve_exec_from_login_shell(self, exe: str) -> Optional[str]:
        quoted_exe = shlex.quote(exe)
        for shell in ("zsh", "bash", "sh"):
            shell_bin = shutil.which(shell)
            if not shell_bin:
                continue
            try:
                proc = subprocess.run(
                    [shell_bin, "-lc", f"command -v {quoted_exe}"],
                    text=True,
                    capture_output=True,
                    timeout=8,
                    check=False,
                    cwd=self.workdir,
                )
            except Exception:
                continue
            if proc.returncode != 0:
                continue
            path = (proc.stdout or "").strip().splitlines()
            if not path:
                continue
            candidate = path[0].strip()
            if self._is_executable(candidate):
                return candidate
        return None

    def ensure_authenticated(self) -> None:
        """Ensure browser-authenticated CLI session is available."""
        if self._auth_checked and self._authenticated:
            return
        if not self.is_available():
            raise BrowserCliError(f"CLI command is not available: {self.command}")
        if not self._auth_check_cmd:
            if self.auto_login and self._login_cmd:
                self._run_interactive_login()
            self._authenticated = True
            self._auth_checked = True
            return

        authenticated = self._check_auth_status()
        if authenticated:
            self._authenticated = True
            self._auth_checked = True
            return

        if not self.auto_login or not self._login_cmd:
            raise BrowserCliError(
                "Browser CLI is not authenticated and auto login is disabled. "
                "Please login manually first."
            )

        self._run_interactive_login()
        authenticated = self._check_auth_status()
        if not authenticated:
            raise BrowserCliError(
                "Browser CLI login did not complete successfully. "
                "Please run login command manually and retry."
            )
        self._authenticated = True
        self._auth_checked = True

    def generate_text(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        self.ensure_authenticated()

        if self._is_codex_cli():
            return self._generate_text_codex_cli(prompt=prompt, system_prompt=system_prompt)

        if self._is_openai_cli():
            return self._generate_text_openai_cli(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )

        payload = {
            "type": "text",
            "model": self.model_name,
            "system_prompt": system_prompt,
            "prompt": prompt,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        return self._generate_via_json_protocol(action="text", payload=payload)

    def generate_vision(
        self,
        image_b64: str,
        mime_type: str,
        prompt: str,
        max_output_tokens: int = 1024,
    ) -> str:
        self.ensure_authenticated()

        if self._is_codex_cli():
            return self._generate_vision_codex_cli(
                image_b64=image_b64,
                mime_type=mime_type,
                prompt=prompt,
            )

        if self._is_openai_cli():
            return self._generate_vision_openai_cli(
                image_b64=image_b64,
                mime_type=mime_type,
                prompt=prompt,
                max_output_tokens=max_output_tokens,
            )

        payload = {
            "type": "vision",
            "model": self.model_name,
            "prompt": prompt,
            "max_output_tokens": max_output_tokens,
            "image": {
                "mime_type": mime_type,
                "base64": image_b64,
            },
        }
        return self._generate_via_json_protocol(action="vision", payload=payload)

    def _is_openai_cli(self) -> bool:
        if not self._base_cmd:
            return False
        return self._base_cmd[0].endswith("openai")

    def _is_codex_cli(self) -> bool:
        if not self._base_cmd:
            return False
        return self._base_cmd[0].endswith("codex")

    def _build_codex_prompt(self, user_prompt: str, system_prompt: str = "") -> str:
        if system_prompt:
            return (
                "You are a model backend used by another application.\n"
                "Follow the system instruction and return only the final answer text.\n\n"
                f"[System]\n{system_prompt}\n\n[User]\n{user_prompt}"
            )
        return user_prompt

    def _read_text_file(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return (f.read() or "").strip()
        except Exception:
            return ""

    def _generate_text_codex_cli(self, prompt: str, system_prompt: str) -> str:
        with tempfile.NamedTemporaryFile(prefix="dsa_codex_out_", suffix=".txt", delete=False) as out_file:
            out_path = out_file.name
        cmd = self._base_cmd + [
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--model",
            self.model_name,
            "--output-last-message",
            out_path,
            self._build_codex_prompt(prompt, system_prompt),
        ]
        try:
            stdout = self._run_command(cmd=cmd, timeout_sec=max(self.timeout_sec, 120))
            text = self._read_text_file(out_path) or self._extract_text_from_output(stdout)
            if not text:
                raise BrowserCliError("codex exec returned empty output")
            return text
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass

    def _generate_vision_codex_cli(self, image_b64: str, mime_type: str, prompt: str) -> str:
        suffix_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        suffix = suffix_map.get((mime_type or "").lower(), ".img")
        with tempfile.NamedTemporaryFile(prefix="dsa_codex_img_", suffix=suffix, delete=False) as img_file:
            img_path = img_file.name
            img_file.write(base64.b64decode(image_b64))
        with tempfile.NamedTemporaryFile(prefix="dsa_codex_out_", suffix=".txt", delete=False) as out_file:
            out_path = out_file.name

        cmd = self._base_cmd + [
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--model",
            self.model_name,
            "--image",
            img_path,
            "--output-last-message",
            out_path,
            self._build_codex_prompt(prompt),
        ]
        try:
            stdout = self._run_command(cmd=cmd, timeout_sec=max(self.timeout_sec, 180))
            text = self._read_text_file(out_path) or self._extract_text_from_output(stdout)
            if not text:
                raise BrowserCliError("codex exec vision returned empty output")
            return text
        finally:
            for path in (img_path, out_path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _generate_text_openai_cli(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        last_error: Optional[Exception] = None
        combined_prompt = f"[System]\n{system_prompt}\n\n[User]\n{prompt}"
        command_variants = [
            self._base_cmd
            + [
                "api",
                "chat.completions.create",
                "-m",
                self.model_name,
                "--temperature",
                f"{temperature}",
                "--max-tokens",
                str(max_output_tokens),
                "-g",
                "system",
                system_prompt,
                "-g",
                "user",
                prompt,
            ],
            self._base_cmd
            + [
                "chat.completions.create",
                "-m",
                self.model_name,
                "--temperature",
                f"{temperature}",
                "--max-tokens",
                str(max_output_tokens),
                "-g",
                "system",
                system_prompt,
                "-g",
                "user",
                prompt,
            ],
            self._base_cmd
            + [
                "responses.create",
                "--model",
                self.model_name,
                "--temperature",
                f"{temperature}",
                "--max-output-tokens",
                str(max_output_tokens),
                "--input",
                combined_prompt,
            ],
        ]
        for cmd in command_variants:
            try:
                stdout = self._run_command(cmd=cmd)
                return self._extract_text_from_output(stdout)
            except Exception as exc:  # pragma: no cover - runtime compatibility path
                last_error = exc
                continue

        raise BrowserCliError(f"OpenAI CLI text call failed: {last_error}")

    def _generate_vision_openai_cli(
        self,
        image_b64: str,
        mime_type: str,
        prompt: str,
        max_output_tokens: int,
    ) -> str:
        data_url = f"data:{mime_type};base64,{image_b64}"
        input_payload = json.dumps(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ],
            ensure_ascii=False,
        )
        command_variants = [
            self._base_cmd
            + [
                "responses.create",
                "--model",
                self.model_name,
                "--max-output-tokens",
                str(max_output_tokens),
                "--input",
                input_payload,
            ]
        ]

        last_error: Optional[Exception] = None
        for cmd in command_variants:
            try:
                stdout = self._run_command(cmd=cmd)
                return self._extract_text_from_output(stdout)
            except Exception as exc:  # pragma: no cover - runtime compatibility path
                last_error = exc
                continue
        raise BrowserCliError(f"OpenAI CLI vision call failed: {last_error}")

    def _generate_via_json_protocol(self, action: str, payload: Dict[str, Any]) -> str:
        if not self._base_cmd:
            raise BrowserCliError("CLI command is empty")

        cmd = [token.format(action=action, model=self.model_name) for token in self._base_cmd]
        if "{action}" not in self.command:
            cmd = cmd + [action]

        stdout = self._run_command(cmd=cmd, stdin_data=json.dumps(payload, ensure_ascii=False))
        return self._extract_text_from_output(stdout)

    def _check_auth_status(self) -> bool:
        try:
            output = self._run_command(
                cmd=self._auth_check_cmd,
                stdin_data=None,
                allow_nonzero=True,
                timeout_sec=min(self.timeout_sec, 30),
            )
            lower_output = (output or "").lower()
            if "not logged in" in lower_output:
                return False
            if "login required" in lower_output:
                return False
            if "logged in" in lower_output or "authenticated" in lower_output:
                return True
            return False
        except Exception:
            return False

    def _run_interactive_login(self) -> None:
        try:
            proc = subprocess.run(
                self._login_cmd,
                text=True,
                capture_output=False,
                timeout=max(self.timeout_sec, 300),
                check=False,
                cwd=self.workdir,
            )
        except Exception as e:
            raise BrowserCliError(f"Failed to run login command: {e}") from e
        if proc.returncode != 0:
            raise BrowserCliError(
                f"Login command exited with code {proc.returncode}: {' '.join(self._login_cmd)}"
            )

    def _run_command(
        self,
        cmd: List[str],
        stdin_data: Optional[str] = None,
        allow_nonzero: bool = False,
        timeout_sec: Optional[int] = None,
    ) -> str:
        proc = subprocess.run(
            cmd,
            input=stdin_data,
            text=True,
            capture_output=True,
            timeout=timeout_sec or self.timeout_sec,
            check=False,
            cwd=self.workdir,
        )
        if proc.returncode != 0 and not allow_nonzero:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            raise BrowserCliError(
                f"CLI exit code {proc.returncode}, stderr: {stderr[:240]}, stdout: {stdout[:240]}"
            )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        output = "\n".join(part for part in (stdout, stderr) if part).strip()
        if not output and not allow_nonzero:
            raise BrowserCliError("CLI returned empty output")
        return output

    @staticmethod
    def _extract_text_from_output(output: str) -> str:
        raw = output.strip()
        try:
            data = json.loads(raw)
        except Exception:
            return raw

        if isinstance(data, dict):
            direct_text = data.get("output_text") or data.get("text")
            if isinstance(direct_text, str) and direct_text.strip():
                return direct_text.strip()

            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    message = first.get("message", {})
                    if isinstance(message, dict):
                        content = message.get("content")
                        if isinstance(content, str) and content.strip():
                            return content.strip()

            output_items = data.get("output")
            if isinstance(output_items, list):
                texts: List[str] = []
                for item in output_items:
                    if not isinstance(item, dict):
                        continue
                    content = item.get("content")
                    if not isinstance(content, list):
                        continue
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        text_val = part.get("text")
                        if isinstance(text_val, str) and text_val.strip():
                            texts.append(text_val.strip())
                if texts:
                    return "\n".join(texts)

        if isinstance(data, list):
            joined = "\n".join(str(item) for item in data if item is not None).strip()
            if joined:
                return joined
        return raw
