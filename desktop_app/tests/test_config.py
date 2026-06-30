from __future__ import annotations

"""Regression tests for BahiAI runtime configuration helpers."""

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from desktop_app import config


class ConfigTests(unittest.TestCase):
    def test_default_runtime_uses_bahiai_appdata_and_database_name(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with (
                patch.dict(os.environ, {"LOCALAPPDATA": temp_dir}, clear=False),
                patch("desktop_app.config.sys.platform", "win32"),
                patch("desktop_app.config.is_portable_mode", return_value=False),
            ):
                runtime_dir = config.app_data_dir()

        self.assertEqual(runtime_dir, Path(temp_dir) / "BahiAI")
        self.assertTrue(config.DATABASE_URL.endswith("bahiai.db"))

    def test_portable_mode_uses_executable_adjacent_data_dir(self) -> None:
        with TemporaryDirectory() as temp_dir:
            exe_dir = Path(temp_dir)
            (exe_dir / "portable.txt").write_text("", encoding="utf-8")
            with patch("desktop_app.config.executable_dir", return_value=exe_dir):
                self.assertTrue(config.is_portable_mode())
                self.assertEqual(config.app_data_dir(), exe_dir / "data")

    def test_env_priority_prefers_executable_adjacent_file(self) -> None:
        with TemporaryDirectory() as exe_temp, TemporaryDirectory() as appdata_temp:
            exe_dir = Path(exe_temp)
            appdata_dir = Path(appdata_temp) / "BahiAI"
            appdata_dir.mkdir()
            exe_env = exe_dir / ".env"
            appdata_env = appdata_dir / ".env"
            exe_env.write_text("GOOGLE_API_KEY=exe-key\n", encoding="utf-8")
            appdata_env.write_text("GOOGLE_API_KEY=appdata-key\n", encoding="utf-8")
            with (
                patch("desktop_app.config.executable_dir", return_value=exe_dir),
                patch("desktop_app.config.default_app_data_dir", return_value=appdata_dir),
            ):
                self.assertEqual(config.app_env_path(), exe_env)

    def test_get_gemini_config_reloads_changed_env_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("GOOGLE_API_KEY=first\nGEMINI_MODEL=model-a\n", encoding="utf-8")
            with patch("desktop_app.config.app_env_path", return_value=env_path):
                self.assertEqual(config.get_gemini_config(), ("first", "model-a"))
                env_path.write_text("GOOGLE_API_KEY=second\nGEMINI_MODEL=model-b\n", encoding="utf-8")
                self.assertEqual(config.get_gemini_config(), ("second", "model-b"))


if __name__ == "__main__":
    unittest.main()
