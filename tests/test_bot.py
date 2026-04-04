from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ragtag_crew.telegram import bot as bot_module


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class FakeSentMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.edit_calls: list[dict[str, object]] = []

    async def edit_text(self, text: str, **kwargs):  # type: ignore[no-untyped-def]
        self.text = text
        self.edit_calls.append({"text": text, **kwargs})
        return self


class FakeMessage:
    def __init__(self, text: str | None = None, placeholder: FakeSentMessage | None = None) -> None:
        self.text = text
        self.reply_calls: list[dict[str, object]] = []
        self._placeholder = placeholder or FakeSentMessage()

    async def reply_text(self, text: str, **kwargs):  # type: ignore[no-untyped-def]
        self.reply_calls.append({"text": text, **kwargs})
        self._placeholder.text = text
        return self._placeholder


class FakeContext:
    def __init__(self, args: list[str] | None = None) -> None:
        self.args = args or []


class FakeUpdate:
    def __init__(self, user_id: int = 1, chat_id: int = 100, text: str | None = None, placeholder: FakeSentMessage | None = None) -> None:
        self.effective_user = FakeUser(user_id)
        self.effective_chat = FakeChat(chat_id)
        self.message = FakeMessage(text=text, placeholder=placeholder)


class BotHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        bot_module._sessions.clear()

    def tearDown(self) -> None:
        bot_module._sessions.clear()

    async def test_cmd_start_replies_with_commands(self) -> None:
        update = FakeUpdate()

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True):
            await bot_module._cmd_start(update, FakeContext())

        self.assertEqual(len(update.message.reply_calls), 1)
        reply = update.message.reply_calls[0]["text"]
        self.assertIn("/skills", reply)
        self.assertIn("/skill", reply)
        self.assertIn("/memory", reply)
        self.assertIn("/context", reply)
        self.assertIn("/mcp", reply)

    async def test_cmd_start_ignores_unauthorized_user(self) -> None:
        update = FakeUpdate(user_id=99)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=False):
            await bot_module._cmd_start(update, FakeContext())

        self.assertEqual(update.message.reply_calls, [])

    async def test_cmd_new_busy_session_is_rejected(self) -> None:
        session = SimpleNamespace(is_busy=True, reset=AsyncMock())
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.delete_session"
        ) as delete_session:
            await bot_module._cmd_new(update, FakeContext())

        self.assertEqual(update.message.reply_calls[0]["text"], "Please wait — agent is busy.")
        session.reset.assert_not_awaited()
        delete_session.assert_not_called()

    async def test_cmd_new_resets_and_deletes_session(self) -> None:
        session = SimpleNamespace(is_busy=False, reset=lambda: None)
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.delete_session"
        ) as delete_session:
            await bot_module._cmd_new(update, FakeContext())

        delete_session.assert_called_once_with(100)
        self.assertEqual(update.message.reply_calls[0]["text"], "Session cleared.")

    async def test_cmd_model_without_args_shows_current_model(self) -> None:
        session = SimpleNamespace(model="openai/GLM-5.1")
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True):
            await bot_module._cmd_model(update, FakeContext())

        self.assertIn("Current model: openai/GLM-5.1", update.message.reply_calls[0]["text"])

    async def test_cmd_model_validation_failure_keeps_previous_model(self) -> None:
        session = SimpleNamespace(model="openai/GLM-5.1")
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.validate_model", side_effect=RuntimeError("boom")
        ), patch("ragtag_crew.telegram.bot.save_session") as save_session:
            await bot_module._cmd_model(update, FakeContext(["openai/gpt-4.1"]))

        self.assertEqual(session.model, "openai/GLM-5.1")
        self.assertIn("Model validation failed", update.message.reply_calls[0]["text"])
        save_session.assert_not_called()

    async def test_cmd_model_validation_success_updates_session(self) -> None:
        session = SimpleNamespace(model="openai/GLM-5.1")
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.validate_model", new=AsyncMock(return_value="OK")
        ), patch("ragtag_crew.telegram.bot.save_session") as save_session:
            await bot_module._cmd_model(update, FakeContext(["openai/gpt-4.1"]))

        self.assertEqual(session.model, "openai/gpt-4.1")
        save_session.assert_called_once_with(100, session)
        self.assertIn("Validation reply: OK", update.message.reply_calls[0]["text"])

    async def test_cmd_tools_without_args_lists_active_tools(self) -> None:
        session = SimpleNamespace(tools=[SimpleNamespace(name="read"), SimpleNamespace(name="grep")])
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True):
            await bot_module._cmd_tools(update, FakeContext())

        self.assertIn("Active tools: read, grep", update.message.reply_calls[0]["text"])

    async def test_cmd_tools_unknown_preset_shows_error(self) -> None:
        session = SimpleNamespace(tools=[], tool_preset="coding")
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.get_tools_for_preset", side_effect=KeyError("bad")
        ):
            await bot_module._cmd_tools(update, FakeContext(["bad"]))

        self.assertIn("Unknown preset: bad", update.message.reply_calls[0]["text"])

    async def test_cmd_tools_updates_session_and_persists(self) -> None:
        session = SimpleNamespace(tools=[], tool_preset="coding")
        new_tools = [SimpleNamespace(name="read"), SimpleNamespace(name="find")]
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.get_tools_for_preset", return_value=new_tools
        ), patch("ragtag_crew.telegram.bot.save_session") as save_session:
            await bot_module._cmd_tools(update, FakeContext(["readonly"]))

        self.assertEqual(session.tools, new_tools)
        self.assertEqual(session.tool_preset, "readonly")
        save_session.assert_called_once_with(100, session)
        self.assertIn("Tools switched to 'readonly'", update.message.reply_calls[0]["text"])

    async def test_cmd_skills_without_local_skills_shows_empty_message(self) -> None:
        session = SimpleNamespace(enabled_skills=[])
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.list_skills", return_value=[]
        ):
            await bot_module._cmd_skills(update, FakeContext())

        self.assertIn("No local skills found", update.message.reply_calls[0]["text"])

    async def test_cmd_skill_use_updates_session_and_persists(self) -> None:
        session = SimpleNamespace(enabled_skills=[])
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)
        skill = SimpleNamespace(name="review")

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.get_skill", return_value=skill
        ), patch("ragtag_crew.telegram.bot.save_session") as save_session:
            await bot_module._cmd_skill(update, FakeContext(["use", "review"]))

        self.assertEqual(session.enabled_skills, ["review"])
        save_session.assert_called_once_with(100, session)
        self.assertEqual(update.message.reply_calls[0]["text"], "Enabled skill: review")

    async def test_cmd_skill_drop_and_clear_update_session(self) -> None:
        session = SimpleNamespace(enabled_skills=["review", "debug"])
        bot_module._sessions[100] = session

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.get_skill", return_value=SimpleNamespace(name="review")
        ), patch("ragtag_crew.telegram.bot.save_session") as save_session:
            drop_update = FakeUpdate(chat_id=100)
            await bot_module._cmd_skill(drop_update, FakeContext(["drop", "review"]))
            clear_update = FakeUpdate(chat_id=100)
            await bot_module._cmd_skill(clear_update, FakeContext(["clear"]))

        self.assertEqual(drop_update.message.reply_calls[0]["text"], "Disabled skill: review")
        self.assertEqual(clear_update.message.reply_calls[0]["text"], "Cleared all active skills.")
        self.assertEqual(session.enabled_skills, [])
        self.assertEqual(save_session.call_count, 2)

    async def test_cmd_memory_without_args_shows_index_and_files(self) -> None:
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.read_memory_index", return_value="short index"
        ), patch("ragtag_crew.telegram.bot.list_memory_files", return_value=["preferences.md"]):
            await bot_module._cmd_memory(update, FakeContext())

        reply = update.message.reply_calls[0]["text"]
        self.assertIn("short index", reply)
        self.assertIn("preferences.md", reply)

    async def test_cmd_memory_add_appends_note(self) -> None:
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.append_memory_note", return_value=SimpleNamespace(parent=SimpleNamespace(name="memory"), name="inbox.md")
        ) as append_note:
            await bot_module._cmd_memory(update, FakeContext(["add", "remember", "this"]))

        append_note.assert_called_once_with("remember this")
        self.assertEqual(update.message.reply_calls[0]["text"], "Added memory note to memory/inbox.md")

    async def test_cmd_memory_show_returns_content(self) -> None:
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.read_memory_file", return_value="saved memory content"
        ) as read_file:
            await bot_module._cmd_memory(update, FakeContext(["show", "preferences"]))

        read_file.assert_called_once_with("preferences")
        self.assertEqual(update.message.reply_calls[0]["text"], "saved memory content")

    async def test_cmd_memory_promote_uses_default_target(self) -> None:
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.promote_inbox", return_value=(SimpleNamespace(name="MEMORY.md", parent=SimpleNamespace(name="repo")), 2)
        ) as promote:
            await bot_module._cmd_memory(update, FakeContext(["promote"]))

        promote.assert_called_once_with("MEMORY.md")
        self.assertEqual(update.message.reply_calls[0]["text"], "Promoted 2 inbox entries to MEMORY.md")

    async def test_cmd_memory_promote_supports_named_target(self) -> None:
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.promote_inbox", return_value=(SimpleNamespace(name="preferences.md", parent=SimpleNamespace(name="memory")), 1)
        ) as promote:
            await bot_module._cmd_memory(update, FakeContext(["promote", "preferences"]))

        promote.assert_called_once_with("preferences")
        self.assertEqual(update.message.reply_calls[0]["text"], "Promoted 1 inbox entry to memory/preferences.md")

    async def test_cmd_context_without_args_shows_summary_status(self) -> None:
        session = SimpleNamespace(
            messages=[{"role": "user", "content": "hi"}],
            session_summary="older work",
            summary_updated_at=None,
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True):
            await bot_module._cmd_context(update, FakeContext())

        reply = update.message.reply_calls[0]["text"]
        self.assertIn("Context status:", reply)
        self.assertIn("older work", reply)
        self.assertIn("/context compress", reply)

    async def test_cmd_context_compress_persists_when_changed(self) -> None:
        session = SimpleNamespace(
            is_busy=False,
            messages=[{"role": "user", "content": "hi"}],
            session_summary="compacted summary",
            summary_updated_at=None,
            compact=lambda force=False: True,
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.save_session"
        ) as save_session:
            await bot_module._cmd_context(update, FakeContext(["compress"]))

        save_session.assert_called_once_with(100, session)
        self.assertIn("Context compacted.", update.message.reply_calls[0]["text"])

    async def test_cmd_context_compress_reports_noop(self) -> None:
        session = SimpleNamespace(
            is_busy=False,
            messages=[],
            session_summary="",
            summary_updated_at=None,
            compact=lambda force=False: False,
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.save_session"
        ) as save_session:
            await bot_module._cmd_context(update, FakeContext(["compress"]))

        save_session.assert_called_once_with(100, session)
        self.assertIn("No compaction needed yet.", update.message.reply_calls[0]["text"])

    async def test_cmd_context_compress_rejects_busy_session(self) -> None:
        session = SimpleNamespace(is_busy=True)
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.save_session"
        ) as save_session:
            await bot_module._cmd_context(update, FakeContext(["compress"]))

        save_session.assert_not_called()
        self.assertEqual(update.message.reply_calls[0]["text"], "Please wait — agent is busy.")

    async def test_cmd_mcp_without_config_shows_hint(self) -> None:
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.get_mcp_statuses", return_value=[]
        ):
            await bot_module._cmd_mcp(update, FakeContext())

        self.assertIn("No MCP servers configured.", update.message.reply_calls[0]["text"])

    async def test_cmd_mcp_lists_server_statuses(self) -> None:
        update = FakeUpdate(chat_id=100)
        statuses = [
            SimpleNamespace(
                key="mcp:filesystem",
                ready=True,
                detail="command=npx",
                tool_names=("mcp_fs_read_file",),
            )
        ]

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.get_mcp_statuses", return_value=statuses
        ):
            await bot_module._cmd_mcp(update, FakeContext())

        reply = update.message.reply_calls[0]["text"]
        self.assertIn("mcp:filesystem", reply)
        self.assertIn("mcp_fs_read_file", reply)

    async def test_handle_message_busy_session_is_rejected(self) -> None:
        session = SimpleNamespace(is_busy=True)
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100, text="hello")

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True):
            await bot_module._handle_message(update, FakeContext())

        self.assertEqual(update.message.reply_calls[0]["text"], "Please wait for the current response to finish.")

    async def test_handle_message_runs_prompt_and_finalizes_streamer(self) -> None:
        placeholder = FakeSentMessage()
        update = FakeUpdate(chat_id=100, text="hello", placeholder=placeholder)
        session = SimpleNamespace(
            is_busy=False,
            prompt=AsyncMock(),
            subscribe=lambda cb: None,
            unsubscribe=lambda cb: None,
        )
        bot_module._sessions[100] = session
        streamer = SimpleNamespace(on_event=object(), finalize=AsyncMock())

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.TelegramStreamer", return_value=streamer
        ), patch("ragtag_crew.telegram.bot.save_session") as save_session, patch(
            "ragtag_crew.telegram.bot.log"
        ):
            await bot_module._handle_message(update, FakeContext())

        session.prompt.assert_awaited_once_with("hello")
        streamer.finalize.assert_awaited_once()
        save_session.assert_called_once_with(100, session)
        self.assertEqual(update.message.reply_calls[0]["text"], "Thinking...")

    async def test_handle_message_error_updates_placeholder_and_still_persists(self) -> None:
        placeholder = FakeSentMessage()
        update = FakeUpdate(chat_id=100, text="hello", placeholder=placeholder)
        session = SimpleNamespace(
            is_busy=False,
            prompt=AsyncMock(side_effect=RuntimeError("broken")),
            subscribe=lambda cb: None,
            unsubscribe=lambda cb: None,
        )
        bot_module._sessions[100] = session
        streamer = SimpleNamespace(on_event=object(), finalize=AsyncMock())

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True), patch(
            "ragtag_crew.telegram.bot.TelegramStreamer", return_value=streamer
        ), patch("ragtag_crew.telegram.bot.save_session") as save_session:
            await bot_module._handle_message(update, FakeContext())

        self.assertEqual(placeholder.edit_calls[0]["text"], "Error: broken")
        streamer.finalize.assert_awaited_once()
        save_session.assert_called_once_with(100, session)

    async def test_get_session_uses_restored_session(self) -> None:
        restored = object()

        with patch("ragtag_crew.telegram.bot.load_session", return_value=restored):
            session = bot_module._get_session(100)

        self.assertIs(session, restored)

    async def test_build_app_registers_expected_handlers(self) -> None:
        added_handlers: list[object] = []

        class FakeBuilder:
            def token(self, value: str) -> "FakeBuilder":
                self.value = value
                return self

            def build(self) -> object:
                return SimpleNamespace(add_handler=lambda handler: added_handlers.append(handler))

        fake_application = SimpleNamespace(builder=lambda: FakeBuilder())

        with patch("ragtag_crew.telegram.bot.cleanup_expired_sessions") as cleanup, patch(
            "ragtag_crew.telegram.bot.ensure_external_capabilities_initialized"
        ) as init_external, patch(
            "ragtag_crew.telegram.bot.Application", fake_application
        ):
            app = bot_module.build_app()

        cleanup.assert_called_once()
        init_external.assert_called_once()
        self.assertEqual(len(added_handlers), 10)
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
