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
        planning_enabled: bool | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.tool_preset = tool_preset
        self.enabled_skills = list(enabled_skills or [])
        self.planning_enabled = (
            planning_enabled if planning_enabled is not None else True
        )
        self.is_busy = False
        self.prompt_calls: list[str] = []
        self._callbacks: list[object] = []
        FakeReplSession.last_instance = self

    def subscribe(self, cb) -> None:  # type: ignore[no-untyped-def]
        self._callbacks.append(cb)

    def unsubscribe(self, cb) -> None:  # type: ignore[no-untyped-def]
        if cb in self._callbacks:
            self._callbacks.remove(cb)

    def reset(self) -> None:
        self.prompt_calls.clear()

    def abort(self) -> None:
        self.is_busy = False

    async def prompt(self, text: str) -> str:
        self.prompt_calls.append(text)
        for cb in self._callbacks:
            await cb(
                "tool_execution_start",
                tool_call=SimpleNamespace(name="read", arguments={"path": "README.md"}),
            )
            await cb("message_update", delta="answer")
            await cb("message_end", content="answer")
            await cb("agent_end")
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
            patch.object(main_module.settings, "weixin_enabled", False),
            patch.object(main_module.settings, "default_model", "test-model"),
            patch.object(main_module.settings, "default_tool_preset", "coding"),
            patch.object(main_module.settings, "allowed_user_ids", ""),
            patch.object(main_module.settings, "weixin_allowed_user_ids", ""),
            redirect_stdout(stdout),
        ):
            rc = main_module.main(["--check"])

        self.assertEqual(rc, 1)
        output = stdout.getvalue()
        self.assertIn("FAIL", output)
        self.assertIn("<empty token>", output)

    def test_check_passes_with_token(self) -> None:
        stdout = io.StringIO()
        with (
            patch.object(main_module.settings, "telegram_bot_token", "fake-token"),
            patch.object(main_module.settings, "weixin_enabled", False),
            patch.object(main_module.settings, "default_model", "openai/gpt-4"),
            patch.object(main_module.settings, "default_tool_preset", "coding"),
            patch.object(main_module.settings, "allowed_user_ids", "42"),
            patch.object(main_module.settings, "weixin_allowed_user_ids", ""),
            redirect_stdout(stdout),
        ):
            rc = main_module.main(["--check"])

        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("OK", output)
        self.assertIn("enabled", output)
        self.assertIn("1 user(s)", output)
        self.assertIn("openai/gpt-4", output)

    def test_check_handles_malformed_user_ids(self) -> None:
        stdout = io.StringIO()
        with (
            patch.object(main_module.settings, "telegram_bot_token", "fake-token"),
            patch.object(main_module.settings, "weixin_enabled", False),
            patch.object(main_module.settings, "default_model", "test-model"),
            patch.object(main_module.settings, "default_tool_preset", "coding"),
            patch.object(main_module.settings, "allowed_user_ids", "42,abc,, "),
            patch.object(main_module.settings, "weixin_allowed_user_ids", ""),
            redirect_stdout(stdout),
        ):
            rc = main_module.main(["--check"])

        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("1 user(s)", output)
        self.assertIn("OK", output)

    def test_check_passes_with_weixin_only(self) -> None:
        stdout = io.StringIO()
        with (
            patch.object(main_module.settings, "telegram_bot_token", ""),
            patch.object(main_module.settings, "weixin_enabled", True),
            patch.object(main_module.settings, "default_model", "test-model"),
            patch.object(main_module.settings, "default_tool_preset", "coding"),
            patch.object(main_module.settings, "allowed_user_ids", ""),
            patch.object(main_module.settings, "weixin_allowed_user_ids", "wx-1,wx-2"),
            redirect_stdout(stdout),
        ):
            rc = main_module.main(["--check"])

        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("weixin      : enabled", output)
        self.assertIn("wx ids      : 2 user(s)", output)

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
            side_effect=lambda: print(
                "Saved sessions:\n\n- session_key=100", file=stdout
            ),
        ):
            rc = main_module.main(["--history-list"])

        self.assertEqual(rc, 0)
        self.assertIn("Saved sessions:", stdout.getvalue())

    def test_history_prints_session_summary(self) -> None:
        stdout = io.StringIO()
        with patch(
            "ragtag_crew.main._show_history",
            side_effect=lambda session_key: print(
                f"Session {session_key}\n\nsession_summary: hi", file=stdout
            ),
        ):
            rc = main_module.main(["--history", "weixin:abc"])

        self.assertEqual(rc, 0)
        out = stdout.getvalue()
        self.assertIn("Session weixin:abc", out)
        self.assertIn("session_summary: hi", out)

    def test_run_telegram_frontend_stops_on_keyboard_interrupt(self) -> None:
        app = SimpleNamespace(
            run_polling=lambda **kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
        )

        with patch("ragtag_crew.telegram.bot.build_app", return_value=app):
            rc = main_module._run_telegram_frontend()

        self.assertEqual(rc, 0)

    def test_run_telegram_frontend_restarts_after_unexpected_return(self) -> None:
        calls: list[dict[str, object]] = []

        def run_once(**kwargs):
            calls.append(kwargs)
            return None

        def stop_loop(**kwargs):
            raise KeyboardInterrupt()

        app1 = SimpleNamespace(run_polling=run_once)
        app2 = SimpleNamespace(run_polling=stop_loop)

        with (
            patch("ragtag_crew.telegram.bot.build_app", side_effect=[app1, app2]),
            patch("ragtag_crew.main.time.sleep") as sleep,
            patch.object(main_module.settings, "telegram_restart_backoff_min", 2),
            patch.object(main_module.settings, "telegram_restart_backoff_max", 10),
            patch.object(main_module.settings, "telegram_health_stale_seconds", 120),
        ):
            rc = main_module._run_telegram_frontend()

        self.assertEqual(rc, 0)
        self.assertEqual(
            calls, [{"drop_pending_updates": True, "bootstrap_retries": -1}]
        )
        sleep.assert_called_once_with(2)

    def test_main_runs_weixin_only_frontend(self) -> None:
        with (
            patch.object(main_module.settings, "telegram_bot_token", ""),
            patch.object(main_module.settings, "weixin_enabled", True),
            patch.object(main_module.settings, "default_model", "m"),
            patch.object(main_module.settings, "default_tool_preset", "coding"),
            patch.object(main_module.settings, "working_dir", "."),
            patch("ragtag_crew.main._setup_logging"),
            patch(
                "ragtag_crew.main._run_weixin_frontend", return_value=7
            ) as run_weixin,
            patch("ragtag_crew.main._run_telegram_frontend") as run_telegram,
            patch("ragtag_crew.tools.bin_resolver.resolve_binary", return_value="rg"),
        ):
            rc = main_module.main([])

        self.assertEqual(rc, 7)
        run_weixin.assert_called_once_with()
        run_telegram.assert_not_called()

    def test_main_runs_both_frontends(self) -> None:
        fake_thread = SimpleNamespace(start=lambda: None)
        with (
            patch.object(main_module.settings, "telegram_bot_token", "fake-token"),
            patch.object(main_module.settings, "weixin_enabled", True),
            patch.object(main_module.settings, "default_model", "m"),
            patch.object(main_module.settings, "default_tool_preset", "coding"),
            patch.object(main_module.settings, "working_dir", "."),
            patch("ragtag_crew.main._setup_logging"),
            patch(
                "ragtag_crew.main._run_telegram_frontend", return_value=3
            ) as run_telegram,
            patch("ragtag_crew.main._run_weixin_frontend") as run_weixin,
            patch(
                "ragtag_crew.main.threading.Thread", return_value=fake_thread
            ) as thread_ctor,
            patch("ragtag_crew.tools.bin_resolver.resolve_binary", return_value="rg"),
        ):
            rc = main_module.main([])

        self.assertEqual(rc, 3)
        thread_ctor.assert_called_once()
        run_weixin.assert_not_called()
        run_telegram.assert_called_once_with()


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
            patch("ragtag_crew.session_store.load_session", return_value=None),
            patch("ragtag_crew.session_store.save_session"),
            patch("ragtag_crew.session_store.delete_session"),
            patch("ragtag_crew.trace.TraceCollector"),
            patch("builtins.input", side_effect=["hello", "/quit"]),
            redirect_stdout(stdout),
        ):
            await main_module._repl_loop()

        out = stdout.getvalue()
        self.assertIn("⏳ read(path=README.md)", out)
        self.assertIn("answer", out)
        self.assertEqual(FakeReplSession.last_instance.prompt_calls, ["hello"])

    async def test_repl_loop_restores_saved_session(self) -> None:
        stdout = io.StringIO()
        FakeReplSession.last_instance = None
        fake_session = FakeReplSession(
            model="openai/gpt-4",
            tools=[SimpleNamespace(name="read")],
            system_prompt="sys",
            tool_preset="readonly",
        )
        with (
            patch("ragtag_crew.external.ensure_external_capabilities_initialized"),
            patch("ragtag_crew.session_store.load_session", return_value=fake_session),
            patch("ragtag_crew.session_store.save_session"),
            patch("ragtag_crew.trace.TraceCollector"),
            patch("builtins.input", side_effect=["/quit"]),
            redirect_stdout(stdout),
        ):
            await main_module._repl_loop()

        out = stdout.getvalue()
        self.assertIn("session restored", out)
        self.assertIn("openai/gpt-4", out)

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
            patch("ragtag_crew.session_store.load_session", return_value=None),
            patch("ragtag_crew.session_store.save_session"),
            patch("ragtag_crew.session_store.delete_session"),
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
            patch("ragtag_crew.session_store.load_session", return_value=None),
            patch("ragtag_crew.session_store.save_session"),
            patch("ragtag_crew.session_store.delete_session"),
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

    async def test_repl_loop_plan_toggle(self) -> None:
        stdout = io.StringIO()
        FakeReplSession.last_instance = None
        with (
            patch("ragtag_crew.external.ensure_external_capabilities_initialized"),
            patch(
                "ragtag_crew.tools.get_tools_for_preset",
                return_value=[SimpleNamespace(name="read")],
            ),
            patch("ragtag_crew.agent.AgentSession", FakeReplSession),
            patch("ragtag_crew.session_store.load_session", return_value=None),
            patch("ragtag_crew.session_store.save_session"),
            patch(
                "builtins.input",
                side_effect=["/plan", "/plan off", "/plan", "/quit"],
            ),
            redirect_stdout(stdout),
        ):
            await main_module._repl_loop()

        out = stdout.getvalue()
        self.assertIn("Current mode: Plan", out)
        self.assertIn("Build mode ON", out)
        self.assertIn("Current mode: Build", out)
        self.assertFalse(FakeReplSession.last_instance.planning_enabled)

    async def test_repl_new_clears_session_and_deletes_store(self) -> None:
        stdout = io.StringIO()
        FakeReplSession.last_instance = None
        with (
            patch("ragtag_crew.external.ensure_external_capabilities_initialized"),
            patch(
                "ragtag_crew.tools.get_tools_for_preset",
                return_value=[SimpleNamespace(name="read")],
            ),
            patch("ragtag_crew.agent.AgentSession", FakeReplSession),
            patch("ragtag_crew.session_store.load_session", return_value=None),
            patch("ragtag_crew.session_store.save_session") as mock_save,
            patch("ragtag_crew.session_store.delete_session") as mock_delete,
            patch("builtins.input", side_effect=["/new", "/quit"]),
            redirect_stdout(stdout),
        ):
            await main_module._repl_loop()

        out = stdout.getvalue()
        self.assertIn("Session cleared.", out)
        mock_delete.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
