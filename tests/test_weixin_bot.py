from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ragtag_crew.weixin import bot as weixin_bot_module


class FakeWeixinBot:
    def __init__(self) -> None:
        self.reply = AsyncMock()
        self.send_typing = AsyncMock()


class FakeMessage:
    def __init__(self, text: str, user_id: str = "wx-1") -> None:
        self.text = text
        self.user_id = user_id


class WeixinBotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        weixin_bot_module._sessions.clear()

    def tearDown(self) -> None:
        weixin_bot_module._sessions.clear()

    async def test_progress_query_uses_rendered_progress_text(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("进度怎么样了")
        session = SimpleNamespace(
            is_busy=True,
            render_progress_text=lambda: "turn=2 running",
        )

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch("ragtag_crew.weixin.bot._get_session", return_value=session),
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        bot.reply.assert_awaited_once_with(message, "turn=2 running")

    async def test_help_command_replies_with_session_commands(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("/help")

        with patch("ragtag_crew.weixin.bot._is_authorized", return_value=True):
            await weixin_bot_module.handle_incoming_message(bot, message)

        reply = bot.reply.await_args.args[1]
        self.assertIn("/help", reply)
        self.assertIn("/sessions", reply)
        self.assertIn("/session use <session_key>", reply)

    async def test_session_current_shows_route(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("/session current")
        route = weixin_bot_module.SessionRoute(
            peer_key="weixin:wx-1",
            current_session_key="458749049",
            default_session_key="weixin:wx-1",
            is_overridden=True,
        )

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch("ragtag_crew.weixin.bot._current_route", return_value=route),
            patch(
                "ragtag_crew.weixin.bot._get_session",
                return_value=SimpleNamespace(is_busy=False),
            ),
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        reply = bot.reply.await_args.args[1]
        self.assertIn("Current session: 458749049", reply)
        self.assertIn("Mode: overridden", reply)

    async def test_session_use_rejects_busy_session(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("/session use 458749049")

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.weixin.bot._get_session",
                return_value=SimpleNamespace(is_busy=True),
            ),
            patch("ragtag_crew.weixin.bot.set_session_route") as set_route,
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        set_route.assert_not_called()
        bot.reply.assert_awaited_once_with(message, "Please wait — agent is busy.")

    async def test_session_reset_switches_back_to_default(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("/session reset")
        route = weixin_bot_module.SessionRoute(
            peer_key="weixin:wx-1",
            current_session_key="weixin:wx-1",
            default_session_key="weixin:wx-1",
            is_overridden=False,
        )

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.weixin.bot._get_session",
                return_value=SimpleNamespace(is_busy=False),
            ),
            patch(
                "ragtag_crew.weixin.bot.reset_session_route", return_value=route
            ) as reset_route,
            patch("ragtag_crew.weixin.bot._get_session_by_key") as get_by_key,
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        reset_route.assert_called_once_with(
            frontend="weixin",
            peer_id="wx-1",
            default_session_key="weixin:wx-1",
        )
        get_by_key.assert_called_once_with("weixin:wx-1")
        self.assertIn("Session routing reset.", bot.reply.await_args.args[1])

    async def test_sessions_command_lists_saved_sessions(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("/sessions")

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.weixin.bot._format_saved_sessions",
                return_value="Saved sessions:\n1. 100 | telegram",
            ),
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        self.assertIn("Saved sessions", bot.reply.await_args.args[1])

    async def test_plan_command_updates_session_and_persists(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("/plan off")
        session = SimpleNamespace(planning_enabled=True)

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch("ragtag_crew.weixin.bot._get_session", return_value=session),
            patch("ragtag_crew.weixin.bot.save_session") as save_session,
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        self.assertFalse(session.planning_enabled)
        save_session.assert_called_once_with("weixin:wx-1", session)
        bot.reply.assert_awaited_once_with(
            message, "Build mode ON — 直接执行，不输出计划。"
        )

    async def test_plain_message_runs_agent_and_saves_session(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("hello")
        collector = SimpleNamespace(
            on_event=AsyncMock(),
            finalize=lambda: None,
            set_context=lambda **kwargs: None,
        )
        session = SimpleNamespace(
            is_busy=False,
            model="openai/GLM-5.1",
            tool_preset="coding",
            enabled_skills=["review"],
            planning_enabled=True,
            prompt=AsyncMock(return_value="answer"),
            subscribe=lambda cb: None,
            unsubscribe=lambda cb: None,
        )

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch("ragtag_crew.weixin.bot._get_session", return_value=session),
            patch(
                "ragtag_crew.weixin.bot.TraceCollector", return_value=collector
            ) as trace_cls,
            patch("ragtag_crew.weixin.bot.save_session") as save_session,
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        trace_cls.assert_called_once_with(session_key="weixin:wx-1")
        bot.send_typing.assert_awaited_once_with("wx-1")
        session.prompt.assert_awaited_once_with("hello")
        bot.reply.assert_awaited_once_with(message, "answer")
        save_session.assert_called_once_with("weixin:wx-1", session)

    async def test_plain_message_uses_overridden_session_key(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("hello")
        route = weixin_bot_module.SessionRoute(
            peer_key="weixin:wx-1",
            current_session_key="458749049",
            default_session_key="weixin:wx-1",
            is_overridden=True,
        )
        collector = SimpleNamespace(
            on_event=AsyncMock(),
            finalize=lambda: None,
            set_context=lambda **kwargs: None,
        )
        session = SimpleNamespace(
            is_busy=False,
            model="openai/GLM-5.1",
            tool_preset="coding",
            enabled_skills=[],
            planning_enabled=True,
            prompt=AsyncMock(return_value="answer"),
            subscribe=lambda cb: None,
            unsubscribe=lambda cb: None,
        )

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch("ragtag_crew.weixin.bot._get_session", return_value=session),
            patch("ragtag_crew.weixin.bot._current_route", return_value=route),
            patch(
                "ragtag_crew.weixin.bot.TraceCollector", return_value=collector
            ) as trace_cls,
            patch("ragtag_crew.weixin.bot.save_session") as save_session,
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        trace_cls.assert_called_once_with(session_key="458749049")
        save_session.assert_called_once_with("458749049", session)


if __name__ == "__main__":
    unittest.main()
