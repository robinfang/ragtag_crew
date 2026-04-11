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
    def __init__(
        self, text: str | None = None, placeholder: FakeSentMessage | None = None
    ) -> None:
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
    def __init__(
        self,
        user_id: int = 1,
        chat_id: int = 100,
        text: str | None = None,
        placeholder: FakeSentMessage | None = None,
    ) -> None:
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
        self.assertIn("Hello", reply)
        self.assertIn("输入 / 查看所有可用命令", reply)

    async def test_cmd_start_ignores_unauthorized_user(self) -> None:
        update = FakeUpdate(user_id=99)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=False):
            await bot_module._cmd_start(update, FakeContext())

        self.assertEqual(update.message.reply_calls, [])

    async def test_cmd_new_busy_session_is_rejected(self) -> None:
        session = SimpleNamespace(is_busy=True, reset=AsyncMock())
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch("ragtag_crew.telegram.bot.delete_session") as delete_session,
        ):
            await bot_module._cmd_new(update, FakeContext())

        self.assertEqual(
            update.message.reply_calls[0]["text"], "Please wait — agent is busy."
        )
        session.reset.assert_not_awaited()
        delete_session.assert_not_called()

    async def test_cmd_new_resets_and_deletes_session(self) -> None:
        session = SimpleNamespace(is_busy=False, reset=lambda: None)
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch("ragtag_crew.telegram.bot.delete_session") as delete_session,
        ):
            await bot_module._cmd_new(update, FakeContext())

        delete_session.assert_called_once_with(100)
        self.assertEqual(update.message.reply_calls[0]["text"], "Session cleared.")

    async def test_cmd_model_without_args_shows_current_model(self) -> None:
        session = SimpleNamespace(model="openai/GLM-5.1")
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True):
            await bot_module._cmd_model(update, FakeContext())

        self.assertIn(
            "Current model: openai/GLM-5.1", update.message.reply_calls[0]["text"]
        )

    async def test_cmd_model_validation_failure_keeps_previous_model(self) -> None:
        session = SimpleNamespace(model="openai/GLM-5.1")
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.validate_model",
                side_effect=RuntimeError("boom"),
            ),
            patch("ragtag_crew.telegram.bot.save_session") as save_session,
        ):
            await bot_module._cmd_model(update, FakeContext(["openai/gpt-4.1"]))

        self.assertEqual(session.model, "openai/GLM-5.1")
        self.assertIn("Model validation failed", update.message.reply_calls[0]["text"])
        save_session.assert_not_called()

    async def test_cmd_model_validation_success_updates_session(self) -> None:
        session = SimpleNamespace(model="openai/GLM-5.1")
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.validate_model",
                new=AsyncMock(return_value="OK"),
            ),
            patch("ragtag_crew.telegram.bot.save_session") as save_session,
        ):
            await bot_module._cmd_model(update, FakeContext(["openai/gpt-4.1"]))

        self.assertEqual(session.model, "openai/gpt-4.1")
        save_session.assert_called_once_with(100, session)
        self.assertIn("Validation reply: OK", update.message.reply_calls[0]["text"])

    async def test_cmd_tools_without_args_lists_active_tools(self) -> None:
        session = SimpleNamespace(
            tools=[SimpleNamespace(name="read"), SimpleNamespace(name="grep")],
            tool_preset="coding",
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True):
            await bot_module._cmd_tools(update, FakeContext())

        self.assertIn("Active tools: read, grep", update.message.reply_calls[0]["text"])

    async def test_cmd_tools_unknown_preset_shows_error(self) -> None:
        session = SimpleNamespace(tools=[], tool_preset="coding")
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.get_tools_for_preset",
                side_effect=KeyError("bad"),
            ),
        ):
            await bot_module._cmd_tools(update, FakeContext(["bad"]))

        self.assertIn("Unknown preset: bad", update.message.reply_calls[0]["text"])

    async def test_cmd_tools_updates_session_and_persists(self) -> None:
        session = SimpleNamespace(tools=[], tool_preset="coding")
        new_tools = [SimpleNamespace(name="read"), SimpleNamespace(name="find")]
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.get_tools_for_preset", return_value=new_tools
            ),
            patch("ragtag_crew.telegram.bot.save_session") as save_session,
        ):
            await bot_module._cmd_tools(update, FakeContext(["readonly"]))

        self.assertEqual(session.tools, new_tools)
        self.assertEqual(session.tool_preset, "readonly")
        save_session.assert_called_once_with(100, session)
        self.assertIn(
            "Tools switched to 'readonly'", update.message.reply_calls[0]["text"]
        )

    async def test_cmd_skills_without_local_skills_shows_empty_message(self) -> None:
        session = SimpleNamespace(enabled_skills=[])
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch("ragtag_crew.telegram.bot.list_skills", return_value=[]),
        ):
            await bot_module._cmd_skills(update, FakeContext())

        self.assertIn("No local skills found", update.message.reply_calls[0]["text"])

    async def test_cmd_skill_use_updates_session_and_persists(self) -> None:
        session = SimpleNamespace(enabled_skills=[])
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)
        skill = SimpleNamespace(name="review")

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch("ragtag_crew.telegram.bot.get_skill", return_value=skill),
            patch("ragtag_crew.telegram.bot.save_session") as save_session,
        ):
            await bot_module._cmd_skill(update, FakeContext(["use", "review"]))

        self.assertEqual(session.enabled_skills, ["review"])
        save_session.assert_called_once_with(100, session)
        self.assertEqual(update.message.reply_calls[0]["text"], "Enabled skill: review")

    async def test_cmd_skill_drop_and_clear_update_session(self) -> None:
        session = SimpleNamespace(enabled_skills=["review", "debug"])
        bot_module._sessions[100] = session

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.get_skill",
                return_value=SimpleNamespace(name="review"),
            ),
            patch("ragtag_crew.telegram.bot.save_session") as save_session,
        ):
            drop_update = FakeUpdate(chat_id=100)
            await bot_module._cmd_skill(drop_update, FakeContext(["drop", "review"]))
            clear_update = FakeUpdate(chat_id=100)
            await bot_module._cmd_skill(clear_update, FakeContext(["clear"]))

        self.assertEqual(
            drop_update.message.reply_calls[0]["text"], "Disabled skill: review"
        )
        self.assertEqual(
            clear_update.message.reply_calls[0]["text"], "Cleared all active skills."
        )
        self.assertEqual(session.enabled_skills, [])
        self.assertEqual(save_session.call_count, 2)

    async def test_cmd_memory_without_args_shows_index_and_files(self) -> None:
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.read_memory_index", return_value="short index"
            ),
            patch(
                "ragtag_crew.telegram.bot.list_memory_files",
                return_value=["preferences.md"],
            ),
        ):
            await bot_module._cmd_memory(update, FakeContext())

        reply = update.message.reply_calls[0]["text"]
        self.assertIn("short index", reply)
        self.assertIn("preferences.md", reply)

    async def test_cmd_memory_add_appends_note(self) -> None:
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.append_memory_note",
                return_value=SimpleNamespace(
                    parent=SimpleNamespace(name="memory"), name="inbox.md"
                ),
            ) as append_note,
        ):
            await bot_module._cmd_memory(
                update, FakeContext(["add", "remember", "this"])
            )

        append_note.assert_called_once_with("remember this")
        self.assertEqual(
            update.message.reply_calls[0]["text"],
            "Added memory note to memory/inbox.md",
        )

    async def test_cmd_memory_show_returns_content(self) -> None:
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.read_memory_file",
                return_value="saved memory content",
            ) as read_file,
        ):
            await bot_module._cmd_memory(update, FakeContext(["show", "preferences"]))

        read_file.assert_called_once_with("preferences")
        self.assertEqual(update.message.reply_calls[0]["text"], "saved memory content")

    async def test_cmd_memory_search_returns_hits(self) -> None:
        update = FakeUpdate(chat_id=100)
        hits = [
            SimpleNamespace(file_name="MEMORY.md", line=3, snippet="python packaging"),
            SimpleNamespace(file_name="preferences.md", line=1, snippet="python style"),
        ]

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.search_memory", return_value=hits
            ) as search_memory,
        ):
            await bot_module._cmd_memory(update, FakeContext(["search", "python"]))

        search_memory.assert_called_once_with("python")
        reply = update.message.reply_calls[0]["text"]
        self.assertIn("Memory search results for: python", reply)
        self.assertIn("MEMORY.md:3", reply)
        self.assertIn("preferences.md:1", reply)

    async def test_cmd_memory_search_requires_query(self) -> None:
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True):
            await bot_module._cmd_memory(update, FakeContext(["search"]))

        self.assertEqual(
            update.message.reply_calls[0]["text"], "Usage: /memory search <query>"
        )

    async def test_cmd_memory_search_reports_no_hits(self) -> None:
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch("ragtag_crew.telegram.bot.search_memory", return_value=[]),
        ):
            await bot_module._cmd_memory(update, FakeContext(["search", "missing"]))

        self.assertEqual(
            update.message.reply_calls[0]["text"],
            "No memory results found for: missing",
        )

    async def test_cmd_memory_promote_uses_default_target(self) -> None:
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.promote_inbox",
                return_value=(
                    SimpleNamespace(
                        name="MEMORY.md", parent=SimpleNamespace(name="repo")
                    ),
                    2,
                ),
            ) as promote,
        ):
            await bot_module._cmd_memory(update, FakeContext(["promote"]))

        promote.assert_called_once_with("MEMORY.md")
        self.assertEqual(
            update.message.reply_calls[0]["text"],
            "Promoted 2 inbox entries to MEMORY.md",
        )

    async def test_cmd_memory_promote_supports_named_target(self) -> None:
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.promote_inbox",
                return_value=(
                    SimpleNamespace(
                        name="preferences.md", parent=SimpleNamespace(name="memory")
                    ),
                    1,
                ),
            ) as promote,
        ):
            await bot_module._cmd_memory(
                update, FakeContext(["promote", "preferences"])
            )

        promote.assert_called_once_with("preferences")
        self.assertEqual(
            update.message.reply_calls[0]["text"],
            "Promoted 1 inbox entry to memory/preferences.md",
        )

    async def test_cmd_prompt_show_returns_session_prompt_and_protected_content(
        self,
    ) -> None:
        session = SimpleNamespace(
            session_prompt="answer briefly",
            protected_content="never remove this rule",
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True):
            await bot_module._cmd_prompt(update, FakeContext())

        reply = update.message.reply_calls[0]["text"]
        self.assertIn("answer briefly", reply)
        self.assertIn("never remove this rule", reply)

    async def test_cmd_prompt_set_and_protect_persist_session(self) -> None:
        session = SimpleNamespace(session_prompt="", protected_content="")
        bot_module._sessions[100] = session

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch("ragtag_crew.telegram.bot.save_session") as save_session,
        ):
            set_update = FakeUpdate(chat_id=100)
            await bot_module._cmd_prompt(
                set_update, FakeContext(["set", "be", "concise"])
            )
            protect_update = FakeUpdate(chat_id=100)
            await bot_module._cmd_prompt(
                protect_update, FakeContext(["protect", "keep", "this"])
            )

        self.assertEqual(session.session_prompt, "be concise")
        self.assertEqual(session.protected_content, "keep this")
        self.assertEqual(
            set_update.message.reply_calls[0]["text"], "Session prompt updated."
        )
        self.assertEqual(
            protect_update.message.reply_calls[0]["text"], "Protected content updated."
        )
        self.assertEqual(save_session.call_count, 2)

    async def test_cmd_prompt_clear_and_unprotect(self) -> None:
        session = SimpleNamespace(session_prompt="x", protected_content="y")
        bot_module._sessions[100] = session

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch("ragtag_crew.telegram.bot.save_session"),
        ):
            clear_update = FakeUpdate(chat_id=100)
            await bot_module._cmd_prompt(clear_update, FakeContext(["clear"]))
            unprotect_update = FakeUpdate(chat_id=100)
            await bot_module._cmd_prompt(unprotect_update, FakeContext(["unprotect"]))

        self.assertEqual(session.session_prompt, "")
        self.assertEqual(session.protected_content, "")
        self.assertEqual(
            clear_update.message.reply_calls[0]["text"], "Session prompt cleared."
        )
        self.assertEqual(
            unprotect_update.message.reply_calls[0]["text"],
            "Protected content cleared.",
        )

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
        async def _compact(**_kwargs):  # type: ignore[no-untyped-def]
            return True

        session = SimpleNamespace(
            is_busy=False,
            messages=[{"role": "user", "content": "hi"}],
            session_summary="compacted summary",
            summary_updated_at=None,
            compact=_compact,
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch("ragtag_crew.telegram.bot.save_session") as save_session,
        ):
            await bot_module._cmd_context(update, FakeContext(["compress"]))

        save_session.assert_called_once_with(100, session)
        self.assertIn("Context compacted.", update.message.reply_calls[0]["text"])

    async def test_cmd_context_compress_reports_noop(self) -> None:
        async def _compact(**_kwargs):  # type: ignore[no-untyped-def]
            return False

        session = SimpleNamespace(
            is_busy=False,
            messages=[],
            session_summary="",
            summary_updated_at=None,
            compact=_compact,
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch("ragtag_crew.telegram.bot.save_session") as save_session,
        ):
            await bot_module._cmd_context(update, FakeContext(["compress"]))

        save_session.assert_called_once_with(100, session)
        self.assertIn(
            "No compaction needed yet.", update.message.reply_calls[0]["text"]
        )

    async def test_cmd_context_compress_rejects_busy_session(self) -> None:
        session = SimpleNamespace(is_busy=True)
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch("ragtag_crew.telegram.bot.save_session") as save_session,
        ):
            await bot_module._cmd_context(update, FakeContext(["compress"]))

        save_session.assert_not_called()
        self.assertEqual(
            update.message.reply_calls[0]["text"], "Please wait — agent is busy."
        )

    async def test_cmd_mcp_without_config_shows_hint(self) -> None:
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch("ragtag_crew.telegram.bot.get_mcp_statuses", return_value=[]),
        ):
            await bot_module._cmd_mcp(update, FakeContext())

        self.assertIn(
            "No MCP servers configured.", update.message.reply_calls[0]["text"]
        )

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

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch("ragtag_crew.telegram.bot.get_mcp_statuses", return_value=statuses),
        ):
            await bot_module._cmd_mcp(update, FakeContext())

        reply = update.message.reply_calls[0]["text"]
        self.assertIn("mcp:filesystem", reply)
        self.assertIn("mcp_fs_read_file", reply)

    async def test_cmd_mcp_reload_forces_refresh(self) -> None:
        update = FakeUpdate(chat_id=100)
        statuses = [
            SimpleNamespace(
                key="mcp:filesystem",
                ready=True,
                detail="command=npx",
                tool_names=("mcp_fs_read_file",),
            )
        ]

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.initialize_external_capabilities",
                new=AsyncMock(return_value=statuses),
            ) as reload_caps,
            patch(
                "ragtag_crew.telegram.bot.get_mcp_statuses",
                return_value=statuses,
            ),
        ):
            await bot_module._cmd_mcp(update, FakeContext(["reload"]))

        reload_caps.assert_awaited_once_with(force=True)
        self.assertIn(
            "MCP capabilities reloaded.", update.message.reply_calls[0]["text"]
        )

    async def test_cmd_ext_show_lists_all_capabilities(self) -> None:
        update = FakeUpdate(chat_id=100)
        statuses = [
            SimpleNamespace(
                key="web-search",
                kind="search",
                ready=True,
                detail="provider=serper",
                tool_names=("web_search",),
            ),
            SimpleNamespace(
                key="everything",
                kind="platform",
                ready=False,
                detail="windows-only",
                tool_names=(),
            ),
        ]

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.get_capability_statuses",
                return_value=statuses,
            ),
        ):
            await bot_module._cmd_ext(update, FakeContext())

        reply = update.message.reply_calls[0]["text"]
        self.assertIn("External capabilities:", reply)
        self.assertIn("web-search: ready", reply)
        self.assertIn("everything: disabled", reply)
        self.assertIn("Usage: /ext show | /ext reload", reply)

    async def test_cmd_ext_reload_forces_refresh(self) -> None:
        update = FakeUpdate(chat_id=100)
        statuses = [
            SimpleNamespace(
                key="web-search",
                kind="search",
                ready=True,
                detail="provider=serper",
                tool_names=("web_search",),
            )
        ]

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.initialize_external_capabilities",
                new=AsyncMock(return_value=statuses),
            ) as reload_caps,
            patch(
                "ragtag_crew.telegram.bot.get_capability_statuses",
                return_value=statuses,
            ),
        ):
            await bot_module._cmd_ext(update, FakeContext(["reload"]))

        reload_caps.assert_awaited_once_with(force=True)
        self.assertIn(
            "External capabilities reloaded.", update.message.reply_calls[0]["text"]
        )

    async def test_cmd_browser_status_shows_mode_and_connection(self) -> None:
        session = SimpleNamespace(
            browser_mode="isolated", browser_attached_confirmed=False
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)
        browser_statuses = [
            SimpleNamespace(
                key="browser-isolated",
                kind="browser",
                ready=True,
                detail="profile=data/browser/isolated",
                tool_names=("browser_open",),
            ),
            SimpleNamespace(
                key="browser-attached",
                kind="browser",
                ready=True,
                detail="detached (auto-connect)",
                tool_names=("browser_open",),
            ),
        ]
        runtime = SimpleNamespace(
            session_mode="isolated",
            default_mode="isolated",
            command="agent-browser",
            isolated_profile_dir="data/browser/isolated",
            attached_target="auto-connect",
            attached_connected=False,
        )

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.get_browser_statuses",
                return_value=browser_statuses,
            ),
            patch(
                "ragtag_crew.telegram.bot.get_browser_runtime_state",
                return_value=runtime,
            ),
        ):
            await bot_module._cmd_browser(update, FakeContext())

        reply = update.message.reply_calls[0]["text"]
        self.assertIn("Browser status:", reply)
        self.assertIn("Session mode: isolated", reply)
        self.assertIn("Attached: ready", reply)
        self.assertIn("Attached confirmed: no", reply)
        self.assertIn("path: auto-connect", reply)
        self.assertIn("Connect hint:", reply)

    async def test_cmd_browser_mode_updates_session(self) -> None:
        session = SimpleNamespace(
            browser_mode="isolated", browser_attached_confirmed=True
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)
        browser_statuses = [
            SimpleNamespace(
                key="browser-isolated",
                kind="browser",
                ready=True,
                detail="profile=x",
                tool_names=(),
            ),
            SimpleNamespace(
                key="browser-attached",
                kind="browser",
                ready=True,
                detail="detached (auto-connect)",
                tool_names=(),
            ),
        ]
        runtime = SimpleNamespace(
            session_mode="attached",
            default_mode="isolated",
            command="agent-browser",
            isolated_profile_dir="data/browser/isolated",
            attached_target="auto-connect",
            attached_connected=False,
        )

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.get_browser_statuses",
                return_value=browser_statuses,
            ),
            patch(
                "ragtag_crew.telegram.bot.get_browser_runtime_state",
                return_value=runtime,
            ),
            patch("ragtag_crew.telegram.bot.save_session") as save_session,
            patch.object(bot_module.settings, "browser_attached_enabled", True),
        ):
            await bot_module._cmd_browser(update, FakeContext(["mode", "attached"]))

        self.assertEqual(session.browser_mode, "attached")
        save_session.assert_called_once_with(100, session)
        self.assertIn(
            "Browser mode switched to: attached", update.message.reply_calls[0]["text"]
        )

    async def test_cmd_browser_mode_attached_requires_confirmation(self) -> None:
        session = SimpleNamespace(
            browser_mode="isolated", browser_attached_confirmed=False
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch.object(bot_module.settings, "browser_attached_enabled", True),
            patch.object(
                bot_module.settings, "browser_attached_require_confirmation", True
            ),
            patch("ragtag_crew.telegram.bot.save_session") as save_session,
        ):
            await bot_module._cmd_browser(update, FakeContext(["mode", "attached"]))

        save_session.assert_not_called()
        self.assertIn(
            "requires explicit confirmation first",
            update.message.reply_calls[0]["text"],
        )

    async def test_cmd_browser_confirm_attached_updates_session(self) -> None:
        session = SimpleNamespace(
            browser_mode="isolated", browser_attached_confirmed=False
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)
        browser_statuses = []
        runtime = SimpleNamespace(
            session_mode="isolated",
            default_mode="isolated",
            command="agent-browser",
            isolated_profile_dir="data/browser/isolated",
            attached_target="auto-connect",
            attached_connected=False,
        )

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.get_browser_statuses",
                return_value=browser_statuses,
            ),
            patch(
                "ragtag_crew.telegram.bot.get_browser_runtime_state",
                return_value=runtime,
            ),
            patch("ragtag_crew.telegram.bot.save_session") as save_session,
        ):
            await bot_module._cmd_browser(update, FakeContext(["confirm-attached"]))

        self.assertTrue(session.browser_attached_confirmed)
        save_session.assert_called_once_with(100, session)
        self.assertIn(
            "Attached browser confirmed", update.message.reply_calls[0]["text"]
        )

    async def test_cmd_browser_status_shows_manual_cdp_path(self) -> None:
        session = SimpleNamespace(
            browser_mode="attached", browser_attached_confirmed=True
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)
        browser_statuses = [
            SimpleNamespace(
                key="browser-isolated",
                kind="browser",
                ready=True,
                detail="profile=data/browser/isolated",
                tool_names=("browser_open",),
            ),
            SimpleNamespace(
                key="browser-attached",
                kind="browser",
                ready=True,
                detail="detached (http://127.0.0.1:9222)",
                tool_names=("browser_open",),
            ),
        ]
        runtime = SimpleNamespace(
            session_mode="attached",
            default_mode="isolated",
            command="agent-browser",
            isolated_profile_dir="data/browser/isolated",
            attached_target="http://127.0.0.1:9222",
            attached_connected=False,
        )

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.get_browser_statuses",
                return_value=browser_statuses,
            ),
            patch(
                "ragtag_crew.telegram.bot.get_browser_runtime_state",
                return_value=runtime,
            ),
        ):
            await bot_module._cmd_browser(update, FakeContext())

        reply = update.message.reply_calls[0]["text"]
        self.assertIn("path: manual-cdp", reply)
        self.assertIn("target: http://127.0.0.1:9222", reply)
        self.assertIn("attach via the configured CDP URL", reply)

    async def test_cmd_browser_connect_reloads_external_state(self) -> None:
        session = SimpleNamespace(
            browser_mode="attached", browser_attached_confirmed=True
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)
        browser_statuses = [
            SimpleNamespace(
                key="browser-isolated",
                kind="browser",
                ready=True,
                detail="profile=x",
                tool_names=(),
            ),
            SimpleNamespace(
                key="browser-attached",
                kind="browser",
                ready=True,
                detail="connected via auto-connect",
                tool_names=(),
            ),
        ]
        runtime = SimpleNamespace(
            session_mode="attached",
            default_mode="isolated",
            command="agent-browser",
            isolated_profile_dir="data/browser/isolated",
            attached_target="auto-connect",
            attached_connected=True,
        )

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.connect_attached_browser",
                new=AsyncMock(
                    return_value=(True, "Connected attached browser via auto-connect.")
                ),
            ) as connect_browser,
            patch(
                "ragtag_crew.telegram.bot.initialize_external_capabilities",
                new=AsyncMock(return_value=[]),
            ) as init_caps,
            patch(
                "ragtag_crew.telegram.bot.get_browser_statuses",
                return_value=browser_statuses,
            ),
            patch(
                "ragtag_crew.telegram.bot.get_browser_runtime_state",
                return_value=runtime,
            ),
        ):
            await bot_module._cmd_browser(update, FakeContext(["connect"]))

        connect_browser.assert_awaited_once()
        init_caps.assert_awaited_once_with(force=True)
        self.assertIn(
            "Attached browser connected.", update.message.reply_calls[0]["text"]
        )

    async def test_cmd_browser_disconnect_reloads_external_state(self) -> None:
        session = SimpleNamespace(
            browser_mode="attached", browser_attached_confirmed=True
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100)
        browser_statuses = [
            SimpleNamespace(
                key="browser-isolated",
                kind="browser",
                ready=True,
                detail="profile=x",
                tool_names=(),
            ),
            SimpleNamespace(
                key="browser-attached",
                kind="browser",
                ready=True,
                detail="detached (auto-connect)",
                tool_names=(),
            ),
        ]
        runtime = SimpleNamespace(
            session_mode="attached",
            default_mode="isolated",
            command="agent-browser",
            isolated_profile_dir="data/browser/isolated",
            attached_target="auto-connect",
            attached_connected=False,
        )

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch(
                "ragtag_crew.telegram.bot.disconnect_attached_browser",
                return_value="Detached from current browser.",
            ) as disconnect_browser,
            patch(
                "ragtag_crew.telegram.bot.initialize_external_capabilities",
                new=AsyncMock(return_value=[]),
            ) as init_caps,
            patch(
                "ragtag_crew.telegram.bot.get_browser_statuses",
                return_value=browser_statuses,
            ),
            patch(
                "ragtag_crew.telegram.bot.get_browser_runtime_state",
                return_value=runtime,
            ),
        ):
            await bot_module._cmd_browser(update, FakeContext(["disconnect"]))

        disconnect_browser.assert_called_once()
        init_caps.assert_awaited_once_with(force=True)
        self.assertIn(
            "Detached from current browser.", update.message.reply_calls[0]["text"]
        )

    async def test_handle_message_busy_session_is_rejected(self) -> None:
        session = SimpleNamespace(is_busy=True)
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100, text="hello")

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True):
            await bot_module._handle_message(update, FakeContext())

        self.assertEqual(
            update.message.reply_calls[0]["text"],
            "Please wait for the current response to finish.",
        )

    async def test_handle_message_busy_progress_query_returns_snapshot(self) -> None:
        session = SimpleNamespace(
            is_busy=True,
            render_progress_text=lambda: "任务仍在执行。\n正在执行: write",
        )
        bot_module._sessions[100] = session
        update = FakeUpdate(chat_id=100, text="进展如何")

        with patch("ragtag_crew.telegram.bot._is_authorized", return_value=True):
            await bot_module._handle_message(update, FakeContext())

        self.assertEqual(
            update.message.reply_calls[0]["text"], "任务仍在执行。\n正在执行: write"
        )

    async def test_handle_message_runs_prompt_and_finalizes_streamer(self) -> None:
        placeholder = FakeSentMessage()
        update = FakeUpdate(chat_id=100, text="hello", placeholder=placeholder)
        session = SimpleNamespace(
            is_busy=False,
            model="openai/GLM-5.1",
            tool_preset="coding",
            enabled_skills=[],
            planning_enabled=True,
            prompt=AsyncMock(),
            subscribe=lambda cb: None,
            unsubscribe=lambda cb: None,
        )
        bot_module._sessions[100] = session
        streamer = SimpleNamespace(on_event=object(), finalize=AsyncMock())

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch("ragtag_crew.telegram.bot.TelegramStreamer", return_value=streamer),
            patch("ragtag_crew.telegram.bot.save_session") as save_session,
            patch("ragtag_crew.telegram.bot.log"),
        ):
            await bot_module._handle_message(update, FakeContext())

        session.prompt.assert_awaited_once_with("hello")
        streamer.finalize.assert_awaited_once()
        save_session.assert_called_once_with(100, session)
        self.assertEqual(update.message.reply_calls[0]["text"], "Thinking...")

    async def test_handle_message_error_updates_placeholder_and_still_persists(
        self,
    ) -> None:
        placeholder = FakeSentMessage()
        update = FakeUpdate(chat_id=100, text="hello", placeholder=placeholder)
        session = SimpleNamespace(
            is_busy=False,
            model="openai/GLM-5.1",
            tool_preset="coding",
            enabled_skills=[],
            planning_enabled=True,
            prompt=AsyncMock(side_effect=RuntimeError("broken")),
            subscribe=lambda cb: None,
            unsubscribe=lambda cb: None,
        )
        bot_module._sessions[100] = session
        streamer = SimpleNamespace(on_event=object(), finalize=AsyncMock())

        with (
            patch("ragtag_crew.telegram.bot._is_authorized", return_value=True),
            patch("ragtag_crew.telegram.bot.TelegramStreamer", return_value=streamer),
            patch("ragtag_crew.telegram.bot.save_session") as save_session,
        ):
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
        added_error_handlers: list[object] = []
        request_instances: list[object] = []
        get_updates_request_instances: list[object] = []

        app = SimpleNamespace(
            add_handler=lambda handler: added_handlers.append(handler),
            add_error_handler=lambda handler: added_error_handlers.append(handler),
            bot_data={},
            post_init=None,
            post_stop=None,
        )

        class FakeBuilder:
            def token(self, value: str) -> "FakeBuilder":
                self.value = value
                return self

            def request(self, request: object) -> "FakeBuilder":
                request_instances.append(request)
                return self

            def get_updates_request(self, request: object) -> "FakeBuilder":
                get_updates_request_instances.append(request)
                return self

            def build(self) -> object:
                return app

        fake_application = SimpleNamespace(builder=lambda: FakeBuilder())

        with (
            patch("ragtag_crew.telegram.bot.cleanup_expired_sessions") as cleanup,
            patch(
                "ragtag_crew.telegram.bot.ensure_external_capabilities_initialized"
            ) as init_external,
            patch("ragtag_crew.telegram.bot.Application", fake_application),
            patch(
                "ragtag_crew.telegram.bot.HTTPXRequest",
                side_effect=lambda **kwargs: kwargs,
            ),
            patch(
                "ragtag_crew.telegram.bot.HealthAwareHTTPXRequest",
                side_effect=lambda **kwargs: kwargs,
            ),
        ):
            built_app = bot_module.build_app()

        cleanup.assert_called_once()
        init_external.assert_called_once()
        self.assertEqual(len(added_handlers), 15)
        self.assertEqual(len(added_error_handlers), 1)
        self.assertEqual(len(request_instances), 1)
        self.assertEqual(len(get_updates_request_instances), 1)
        self.assertIsNotNone(app.bot_data.get("telegram_runtime_health"))
        self.assertIsNotNone(app.post_init)
        self.assertIsNotNone(app.post_stop)
        self.assertFalse(request_instances[0]["httpx_kwargs"].get("trust_env", True))
        self.assertFalse(
            get_updates_request_instances[0]["httpx_kwargs"].get("trust_env", True)
        )
        self.assertIs(built_app, app)

    async def test_register_commands(self) -> None:
        fake_bot = AsyncMock()
        app = SimpleNamespace(bot=fake_bot)

        await bot_module._register_commands(app)

        fake_bot.set_my_commands.assert_awaited_once()
        commands = fake_bot.set_my_commands.await_args[0][0]
        command_names = [c.command for c in commands]
        for expected in bot_module._REGISTERED_COMMAND_NAMES:
            self.assertIn(expected, command_names)

    def test_bot_commands_match_handlers(self) -> None:
        handler_commands = [
            "start",
            "new",
            "cancel",
            "plan",
            "model",
            "tools",
            "skills",
            "skill",
            "memory",
            "prompt",
            "context",
            "mcp",
            "ext",
            "browser",
        ]
        for cmd in handler_commands:
            if cmd == "start":
                continue
            self.assertIn(cmd, bot_module._REGISTERED_COMMAND_NAMES)
        self.assertEqual(len(bot_module._BOT_COMMANDS), len(handler_commands) - 1)


if __name__ == "__main__":
    unittest.main()
