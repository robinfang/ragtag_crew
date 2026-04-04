from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from ragtag_crew import main as main_module


class MainCliTests(unittest.TestCase):
    def test_help_shows_usage(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as ctx:
            main_module.main(["-h"])

        self.assertEqual(ctx.exception.code, 0)
        output = stdout.getvalue()
        self.assertIn("usage: ragtag-crew", output)
        self.assertIn("草台班子", output)
        self.assertIn("--check", output)
        self.assertIn("--working-dir", output)
        self.assertIn("--model", output)

    def test_version_flag(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as ctx:
            main_module.main(["-V"])

        self.assertEqual(ctx.exception.code, 0)
        output = stdout.getvalue()
        self.assertIn("ragtag-crew", output)

    def test_check_fails_without_token(self) -> None:
        stdout = io.StringIO()
        with patch.object(main_module.settings, "telegram_bot_token", ""), \
             patch.object(main_module.settings, "default_model", "test-model"), \
             patch.object(main_module.settings, "default_tool_preset", "coding"), \
             patch.object(main_module.settings, "allowed_user_ids", ""), \
             redirect_stdout(stdout):
            rc = main_module.main(["--check"])

        self.assertEqual(rc, 1)
        output = stdout.getvalue()
        self.assertIn("FAIL", output)
        self.assertIn("<empty>", output)

    def test_check_passes_with_token(self) -> None:
        stdout = io.StringIO()
        with patch.object(main_module.settings, "telegram_bot_token", "fake-token"), \
             patch.object(main_module.settings, "default_model", "openai/gpt-4"), \
             patch.object(main_module.settings, "default_tool_preset", "coding"), \
             patch.object(main_module.settings, "allowed_user_ids", "42"), \
             redirect_stdout(stdout):
            rc = main_module.main(["--check"])

        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("OK", output)
        self.assertIn("set", output)
        self.assertIn("1 user(s)", output)
        self.assertIn("openai/gpt-4", output)

    def test_cli_override_working_dir(self) -> None:
        original = main_module.settings.working_dir
        try:
            main_module._apply_cli_overrides(
                main_module.build_arg_parser().parse_args(["--working-dir", "/tmp/project"])
            )
            self.assertEqual(main_module.settings.working_dir, "/tmp/project")
        finally:
            main_module.settings.working_dir = original

    def test_cli_override_model(self) -> None:
        original = main_module.settings.default_model
        try:
            main_module._apply_cli_overrides(
                main_module.build_arg_parser().parse_args(["--model", "claude-3-5-sonnet"])
            )
            self.assertEqual(main_module.settings.default_model, "claude-3-5-sonnet")
        finally:
            main_module.settings.default_model = original

    def test_cli_override_tools(self) -> None:
        original = main_module.settings.default_tool_preset
        try:
            main_module._apply_cli_overrides(
                main_module.build_arg_parser().parse_args(["--tools", "readonly"])
            )
            self.assertEqual(main_module.settings.default_tool_preset, "readonly")
        finally:
            main_module.settings.default_tool_preset = original

    def test_cli_override_log_level(self) -> None:
        original = main_module.settings.log_level
        try:
            main_module._apply_cli_overrides(
                main_module.build_arg_parser().parse_args(["--log-level", "DEBUG"])
            )
            self.assertEqual(main_module.settings.log_level, "DEBUG")
        finally:
            main_module.settings.log_level = original

    def test_cli_no_override_when_args_missing(self) -> None:
        original_wd = main_module.settings.working_dir
        original_model = main_module.settings.default_model
        try:
            main_module._apply_cli_overrides(
                main_module.build_arg_parser().parse_args([])
            )
            self.assertEqual(main_module.settings.working_dir, original_wd)
            self.assertEqual(main_module.settings.default_model, original_model)
        finally:
            main_module.settings.working_dir = original_wd
            main_module.settings.default_model = original_model


if __name__ == "__main__":
    unittest.main()
