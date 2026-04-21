from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ragtag_crew.weixin import bot as weixin_bot_module


class FakeWeixinBot:
    def __init__(self) -> None:
        self.reply = AsyncMock()
        self.send = AsyncMock()
        self.send_typing = AsyncMock()


class FakeMessage:
    def __init__(self, text: str, user_id: str = "wx-1") -> None:
        self.text = text
        self.user_id = user_id


class WeixinBotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        weixin_bot_module._sessions.clear()
        weixin_bot_module._active_prompt_tasks.clear()

    def tearDown(self) -> None:
        weixin_bot_module._sessions.clear()
        weixin_bot_module._active_prompt_tasks.clear()

    async def test_progress_query_uses_rendered_progress_text(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("进度怎么样了")
        route = weixin_bot_module.SessionRoute(
            peer_key="weixin:wx-1",
            current_session_key="weixin:wx-1",
            default_session_key="weixin:wx-1",
            is_overridden=False,
        )
        session = SimpleNamespace(
            is_busy=True,
            render_progress_text=lambda: "turn=2 running",
        )

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch("ragtag_crew.weixin.bot._current_route", return_value=route),
            patch("ragtag_crew.weixin.bot._get_session", return_value=session),
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        bot.reply.assert_awaited_once_with(message, "turn=2 running")

    async def test_progress_query_uses_starting_text_when_task_exists_but_not_busy(
        self,
    ) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("进度怎么样了")
        route = weixin_bot_module.SessionRoute(
            peer_key="weixin:wx-1",
            current_session_key="weixin:wx-1",
            default_session_key="weixin:wx-1",
            is_overridden=False,
        )
        session = SimpleNamespace(
            is_busy=False,
            render_progress_text=lambda: "should not be used",
        )
        task = Mock()
        task.done.return_value = False
        task.cancel = Mock()
        weixin_bot_module._active_prompt_tasks["weixin:wx-1"] = task

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch("ragtag_crew.weixin.bot._current_route", return_value=route),
            patch("ragtag_crew.weixin.bot._get_session", return_value=session),
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        bot.reply.assert_awaited_once_with(message, "任务已提交，正在启动。")

    async def test_help_command_replies_with_session_commands(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("/help")

        with patch("ragtag_crew.weixin.bot._is_authorized", return_value=True):
            await weixin_bot_module.handle_incoming_message(bot, message)

        reply = bot.reply.await_args.args[1]
        self.assertIn("/help", reply)
        self.assertIn("/sessions", reply)
        self.assertIn("/session use <session_key>", reply)
        self.assertIn("/session use <index>", reply)

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
        self.assertIn("/session use <index>", reply)

    async def test_session_use_rejects_busy_session(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("/session use 458749049")
        route = weixin_bot_module.SessionRoute(
            peer_key="weixin:wx-1",
            current_session_key="weixin:wx-1",
            default_session_key="weixin:wx-1",
            is_overridden=False,
        )

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch("ragtag_crew.weixin.bot._current_route", return_value=route),
            patch(
                "ragtag_crew.weixin.bot._get_session",
                return_value=SimpleNamespace(is_busy=True),
            ),
            patch("ragtag_crew.weixin.bot.set_session_route") as set_route,
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        set_route.assert_not_called()
        reply = bot.reply.await_args.args[1]
        self.assertIn("Please wait — agent is busy.", reply)
        self.assertIn("/session use <index>", reply)

    async def test_session_use_switches_by_index(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("/session use 2")
        route = weixin_bot_module.SessionRoute(
            peer_key="weixin:wx-1",
            current_session_key="458749049",
            default_session_key="weixin:wx-1",
            is_overridden=True,
        )
        records = [
            SimpleNamespace(session_key="weixin:wx-1"),
            SimpleNamespace(session_key="458749049"),
        ]

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.weixin.bot._get_session",
                return_value=SimpleNamespace(is_busy=False),
            ),
            patch("ragtag_crew.weixin.bot.list_sessions", return_value=records),
            patch(
                "ragtag_crew.weixin.bot.set_session_route", return_value=route
            ) as set_route,
            patch("ragtag_crew.weixin.bot._get_session_by_key") as get_by_key,
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        set_route.assert_called_once_with(
            frontend="weixin",
            peer_id="wx-1",
            default_session_key="weixin:wx-1",
            session_key="458749049",
        )
        get_by_key.assert_called_once_with("458749049")

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
        reply = bot.reply.await_args.args[1]
        self.assertIn("Session routing reset.", reply)
        self.assertIn("/session use <index>", reply)

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
        clear_pending_plan = Mock()
        session = SimpleNamespace(
            planning_enabled=True,
            clear_pending_plan=clear_pending_plan,
        )

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch("ragtag_crew.weixin.bot._get_session", return_value=session),
            patch("ragtag_crew.weixin.bot.save_session") as save_session,
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        self.assertFalse(session.planning_enabled)
        clear_pending_plan.assert_called_once_with()
        save_session.assert_called_once_with("weixin:wx-1", session)
        bot.reply.assert_awaited_once_with(
            message, "Build mode ON — 直接执行，不输出计划。"
        )

    async def test_plain_message_acknowledges_and_starts_background_task(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("hello")
        route = weixin_bot_module.SessionRoute(
            peer_key="weixin:wx-1",
            current_session_key="458749049",
            default_session_key="weixin:wx-1",
            is_overridden=True,
        )
        session = SimpleNamespace(is_busy=False)
        fake_task = Mock()
        fake_task.done.return_value = False
        fake_task.cancel = Mock()

        def fake_create_task(coro):
            coro.close()
            return fake_task

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch("ragtag_crew.weixin.bot._get_session", return_value=session),
            patch("ragtag_crew.weixin.bot._current_route", return_value=route),
            patch("ragtag_crew.weixin.bot.asyncio.create_task", side_effect=fake_create_task),
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        bot.reply.assert_awaited_once_with(
            message, "已收到，开始处理。可随时发送 /cancel 或直接询问进度。"
        )
        self.assertIs(
            weixin_bot_module._active_prompt_tasks["458749049"],
            fake_task,
        )

    async def test_cancel_command_aborts_session_and_cancels_background_task(
        self,
    ) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("/cancel")
        route = weixin_bot_module.SessionRoute(
            peer_key="weixin:wx-1",
            current_session_key="weixin:wx-1",
            default_session_key="weixin:wx-1",
            is_overridden=False,
        )
        session = SimpleNamespace(is_busy=False, abort=Mock())
        task = Mock()
        task.done.return_value = False
        task.cancel = Mock()
        weixin_bot_module._active_prompt_tasks["weixin:wx-1"] = task

        with (
            patch("ragtag_crew.weixin.bot._is_authorized", return_value=True),
            patch("ragtag_crew.weixin.bot._current_route", return_value=route),
            patch("ragtag_crew.weixin.bot._get_session", return_value=session),
        ):
            await weixin_bot_module.handle_incoming_message(bot, message)

        session.abort.assert_called_once_with()
        task.cancel.assert_called_once_with()
        bot.reply.assert_awaited_once_with(message, "已发送取消信号。")

    async def test_background_runner_sends_result_and_saves_session(self) -> None:
        bot = FakeWeixinBot()
        message = FakeMessage("hello")
        route = weixin_bot_module.SessionRoute(
            peer_key="weixin:wx-1",
            current_session_key="458749049",
            default_session_key="weixin:wx-1",
            is_overridden=True,
        )
        subscriptions: list[object] = []
        unsubscriptions: list[object] = []
        session = SimpleNamespace(
            is_busy=False,
            model="openai/GLM-5.1",
            tool_preset="coding",
            enabled_skills=["review"],
            planning_enabled=True,
            prompt=AsyncMock(return_value="answer"),
            subscribe=lambda cb: subscriptions.append(cb),
            unsubscribe=lambda cb: unsubscriptions.append(cb),
            abort=Mock(),
            render_progress_text=lambda: "running",
        )

        with (
            patch("ragtag_crew.weixin.bot._current_route", return_value=route),
            patch("ragtag_crew.weixin.bot.save_session") as save_session,
        ):
            await weixin_bot_module._run_session_prompt_in_background(
                bot, message, session, "458749049", "hello"
            )

        session.prompt.assert_awaited_once_with("hello")
        bot.send_typing.assert_awaited_once_with("wx-1")
        self.assertGreaterEqual(bot.send.await_count, 2)
        self.assertEqual(bot.send.await_args_list[0].args, ("wx-1", "开始处理，请稍候。"))
        self.assertEqual(bot.send.await_args_list[-1].args, ("wx-1", "answer"))
        save_session.assert_called_once_with("458749049", session)
        self.assertEqual(len(subscriptions), 2)
        self.assertEqual(len(unsubscriptions), 2)


if __name__ == "__main__":
    unittest.main()
