from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from ragtag_crew import main as main_module


class FakeReplSession:
    last_instance: "FakeReplSession | None" = None

    def __init__(
        self,
        model: str,
        tools: list[object],
        system_prompt: str = "",
        tool_preset: str = "coding",
        enabled_skills: list[str] | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.tool_preset = tool_preset
        self.enabled_skills = list(enabled_skills or [])
        self.is_busy = False
        self.prompt_calls: list[str] = []
        self._callback = None
        FakeReplSession.last_instance = self

    def subscribe(self, cb) -> None:  # type: ignore[no-untyped-def]
        self._callback = cb

    def reset(self) -> None:
        self.prompt_calls.clear()

    def abort(self) -> None:
        self.is_busy = False

    async def prompt(self, text: str) -> str:
        self.prompt_calls.append(text)
        if self._callback is not None:
            await self._callback(
                "tool_execution_start",
                tool_call=SimpleNamespace(name="read", arguments={"path": "README.md"}),
            )
            await self._callback("message_update", delta="partial")
            await self._callback("message_end", content="answer")
        return "answer"


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
        with (
            patch.object(main_module.settings, "telegram_bot_token", ""),
            patch.object(main_module.settings, "default_model", "test-model"),
            patch.object(main_module.settings, "default_tool_preset", "coding"),
            patch.object(main_module.settings, "allowed_user_ids", ""),
            redirect_stdout(stdout),
        ):
            rc = main_module.main(["--check"])

        self.assertEqual(rc, 1)
        output = stdout.getvalue()
        self.assertIn("FAIL", output)
        self.assertIn("<empty>", output)

    def test_check_passes_with_token(self) -> None:
        stdout = io.StringIO()
        with (
            patch.object(main_module.settings, "telegram_bot_token", "fake-token"),
            patch.object(main_module.settings, "default_model", "openai/gpt-4"),
            patch.object(main_module.settings, "default_tool_preset", "coding"),
            patch.object(main_module.settings, "allowed_user_ids", "42"),
            redirect_stdout(stdout),
        ):
            rc = main_module.main(["--check"])

        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("OK", output)
        self.assertIn("set", output)
        self.assertIn("1 user(s)", output)
        self.assertIn("openai/gpt-4", output)

    def test_check_handles_malformed_user_ids(self) -> None:
        stdout = io.StringIO()
        with (
            patch.object(main_module.settings, "telegram_bot_token", "fake-token"),
            patch.object(main_module.settings, "default_model", "test-model"),
            patch.object(main_module.settings, "default_tool_preset", "coding"),
            patch.object(main_module.settings, "allowed_user_ids", "42,abc,, "),
            redirect_stdout(stdout),
        ):
            rc = main_module.main(["--check"])

        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("1 user(s)", output)
        self.assertIn("OK", output)

    def test_cli_override_working_dir(self) -> None:
        original = main_module.settings.working_dir
        try:
            main_module._apply_cli_overrides(
                main_module.build_arg_parser().parse_args(
                    ["--working-dir", "/tmp/project"]
                )
            )
            self.assertEqual(main_module.settings.working_dir, "/tmp/project")
        finally:
            main_module.settings.working_dir = original

    def test_cli_override_model(self) -> None:
        original = main_module.settings.default_model
        try:
            main_module._apply_cli_overrides(
                main_module.build_arg_parser().parse_args(
                    ["--model", "claude-3-5-sonnet"]
                )
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

    def test_cli_dev_sets_dev_mode_and_debug(self) -> None:
        original_dev = main_module.settings.dev_mode
        original_log = main_module.settings.log_level
        try:
            main_module._apply_cli_overrides(
                main_module.build_arg_parser().parse_args(["--dev"])
            )
            self.assertTrue(main_module.settings.dev_mode)
            self.assertEqual(main_module.settings.log_level, "DEBUG")
        finally:
            main_module.settings.dev_mode = original_dev
            main_module.settings.log_level = original_log

    def test_cli_dev_does_not_override_explicit_log_level(self) -> None:
        original_dev = main_module.settings.dev_mode
        original_log = main_module.settings.log_level
        try:
            main_module._apply_cli_overrides(
                main_module.build_arg_parser().parse_args(
                    ["--dev", "--log-level", "WARNING"]
                )
            )
            self.assertTrue(main_module.settings.dev_mode)
            self.assertEqual(main_module.settings.log_level, "WARNING")
        finally:
            main_module.settings.dev_mode = original_dev
            main_module.settings.log_level = original_log

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

    def test_history_list_prints_saved_sessions(self) -> None:
        stdout = io.StringIO()
        with patch(
            "ragtag_crew.main._show_history_list",
            side_effect=lambda: print("Saved sessions:\n\n- chat_id=100", file=stdout),
        ):
            rc = main_module.main(["--history-list"])

        self.assertEqual(rc, 0)
        self.assertIn("Saved sessions:", stdout.getvalue())

    def test_history_prints_session_summary(self) -> None:
        stdout = io.StringIO()
        with patch(
            "ragtag_crew.main._show_history",
            side_effect=lambda chat_id: print(
                f"Session {chat_id}\n\nsession_summary: hi", file=stdout
            ),
        ):
            rc = main_module.main(["--history", "123"])

        self.assertEqual(rc, 0)
        out = stdout.getvalue()
        self.assertIn("Session 123", out)
        self.assertIn("session_summary: hi", out)


class MainReplTests(unittest.IsolatedAsyncioTestCase):
    async def test_repl_loop_handles_events_with_async_callback(self) -> None:
        stdout = io.StringIO()
        FakeReplSession.last_instance = None
        with (
            patch("ragtag_crew.external.ensure_external_capabilities_initialized"),
            patch(
                "ragtag_crew.tools.get_tools_for_preset",
                return_value=[SimpleNamespace(name="read")],
            ),
            patch("ragtag_crew.agent.AgentSession", FakeReplSession),
            patch("builtins.input", side_effect=["hello", "/quit"]),
            redirect_stdout(stdout),
        ):
            await main_module._repl_loop()

        out = stdout.getvalue()
        self.assertIn("⏳ read(path=README.md)", out)
        self.assertIn("answer", out)
        self.assertEqual(FakeReplSession.last_instance.prompt_calls, ["hello"])

    async def test_repl_loop_lists_skills_without_calling_prompt(self) -> None:
        stdout = io.StringIO()
        FakeReplSession.last_instance = None
        with (
            patch("ragtag_crew.external.ensure_external_capabilities_initialized"),
            patch(
                "ragtag_crew.tools.get_tools_for_preset",
                return_value=[SimpleNamespace(name="read")],
            ),
            patch("ragtag_crew.agent.AgentSession", FakeReplSession),
            patch.object(
                main_module,
                "list_skills",
                return_value=[
                    SimpleNamespace(name="review", summary="Focus on risks first.")
                ],
            ),
            patch("builtins.input", side_effect=["/skills", "/quit"]),
            redirect_stdout(stdout),
        ):
            await main_module._repl_loop()

        out = stdout.getvalue()
        self.assertIn("Active skills: (none)", out)
        self.assertIn("Available skills:", out)
        self.assertIn("- review - Focus on risks first.", out)
        self.assertEqual(FakeReplSession.last_instance.prompt_calls, [])

    async def test_repl_loop_supports_skill_use_and_clear(self) -> None:
        stdout = io.StringIO()
        FakeReplSession.last_instance = None
        with (
            patch("ragtag_crew.external.ensure_external_capabilities_initialized"),
            patch(
                "ragtag_crew.tools.get_tools_for_preset",
                return_value=[SimpleNamespace(name="read")],
            ),
            patch("ragtag_crew.agent.AgentSession", FakeReplSession),
            patch.object(
                main_module, "get_skill", return_value=SimpleNamespace(name="review")
            ),
            patch(
                "builtins.input",
                side_effect=["/skill use review", "/skill clear", "/quit"],
            ),
            redirect_stdout(stdout),
        ):
            await main_module._repl_loop()

        out = stdout.getvalue()
        self.assertIn("Enabled skill: review", out)
        self.assertIn("Cleared all active skills.", out)
        self.assertEqual(FakeReplSession.last_instance.enabled_skills, [])
        self.assertEqual(FakeReplSession.last_instance.prompt_calls, [])


if __name__ == "__main__":
    unittest.main()
