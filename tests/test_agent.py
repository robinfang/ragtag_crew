from __future__ import annotations

import asyncio
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ragtag_crew.agent import AgentSession
from ragtag_crew.errors import LLMChunkTimeoutError, TurnTimeoutError
from ragtag_crew.llm import LLMResponse, ToolCall
from ragtag_crew.tools import Tool


async def _noop_tool(**_: str) -> str:
    return "ok"


class AgentSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_raises_turn_timeout_and_clears_busy_flag(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )

        async def slow_stream_chat(**kwargs):  # type: ignore[no-untyped-def]
            await asyncio.sleep(0.05)
            return LLMResponse(content="late")

        with (
            patch("ragtag_crew.agent.settings.turn_timeout", 0.01),
            patch(
                "ragtag_crew.agent.stream_chat",
                side_effect=slow_stream_chat,
            ),
        ):
            with self.assertRaises(TurnTimeoutError):
                await session.prompt("hello")

        self.assertFalse(session.is_busy)

    async def test_partial_llm_output_is_kept_on_chunk_timeout(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )

        async def chunk_timeout(**kwargs):  # type: ignore[no-untyped-def]
            raise LLMChunkTimeoutError(
                30, partial_response=LLMResponse(content="hello")
            )

        with patch("ragtag_crew.agent.stream_chat", side_effect=chunk_timeout):
            with self.assertRaises(LLMChunkTimeoutError):
                await session.prompt("hello")

        self.assertEqual(session.messages[-1]["content"], "hello")

    async def test_prompt_compacts_older_messages_into_session_summary(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )
        session.messages = [
            {"role": "user", "content": "old requirement"},
            {"role": "assistant", "content": "old reply"},
            {"role": "tool", "tool_call_id": "call_1", "content": "old tool output"},
        ]

        async def ok_stream(**kwargs):  # type: ignore[no-untyped-def]
            return LLMResponse(content="latest answer")

        with (
            patch("ragtag_crew.agent.stream_chat", side_effect=ok_stream),
            patch("ragtag_crew.agent.settings.session_summary_trigger_messages", 4),
            patch("ragtag_crew.agent.settings.session_summary_recent_messages", 2),
        ):
            result = await session.prompt("new request")

        self.assertEqual(result, "latest answer")
        self.assertEqual(
            session.messages,
            [
                {"role": "user", "content": "new request"},
                {"role": "assistant", "content": "latest answer"},
            ],
        )
        self.assertIn("old requirement", session.session_summary)
        self.assertIn("old reply", session.session_summary)
        self.assertIn("old tool output", session.session_summary)
        self.assertEqual(session.recent_message_count, 2)
        self.assertIsNotNone(session.summary_updated_at)

    def test_compact_summary_keeps_external_tool_metadata(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )
        session.messages = [
            {
                "role": "tool",
                "tool_call_id": "call_search",
                "tool_name": "web_search",
                "tool_source_type": "search",
                "tool_source_name": "serper",
                "content": "Result URL: https://example.com/article Snippet: useful context",
            },
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "next"},
        ]

        with patch("ragtag_crew.agent.settings.session_summary_recent_messages", 2):
            changed = session.compact(force=True)

        self.assertTrue(changed)
        self.assertIn("web_search/search/serper", session.session_summary)
        self.assertIn("https://example.com/article", session.session_summary)

    async def test_prompt_merges_existing_summary_when_compacting_again(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
            session_summary="Earlier work: settled repo structure.",
        )
        session.messages = [
            {"role": "user", "content": "follow-up change"},
            {"role": "assistant", "content": "working on it"},
        ]

        async def ok_stream(**kwargs):  # type: ignore[no-untyped-def]
            return LLMResponse(content="done")

        with (
            patch("ragtag_crew.agent.stream_chat", side_effect=ok_stream),
            patch("ragtag_crew.agent.settings.session_summary_trigger_messages", 3),
            patch("ragtag_crew.agent.settings.session_summary_recent_messages", 2),
        ):
            await session.prompt("compress more")

        self.assertIn("Earlier work: settled repo structure.", session.session_summary)
        self.assertIn("follow-up change", session.session_summary)

    def test_render_progress_text_includes_runtime_snapshot(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )
        session._busy = True
        session._active_started_at = time.monotonic()
        session._active_request_text = "修复回归模块"
        session._active_turn = 2
        session._completed_turns = 1
        session._completed_tools = 3
        session._active_tool_name = "write"
        session._response_preview = "正在补测试"

        text = session.render_progress_text()

        self.assertIn("任务仍在执行。", text)
        self.assertIn("当前请求: 修复回归模块", text)
        self.assertIn("当前轮次: 2", text)
        self.assertIn("已完成轮次: 1", text)
        self.assertIn("已执行工具: 3 次", text)
        self.assertIn("正在执行: write", text)
        self.assertIn("最近输出: 正在补测试", text)

    def test_manual_compact_respects_force_flag(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )
        session.messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
            {"role": "user", "content": "third"},
        ]

        with (
            patch("ragtag_crew.agent.settings.session_summary_trigger_messages", 10),
            patch("ragtag_crew.agent.settings.session_summary_recent_messages", 2),
        ):
            changed = session.compact(force=True)

        self.assertTrue(changed)
        self.assertEqual(len(session.messages), 2)
        self.assertIn("first", session.session_summary)

    def test_precompact_memory_capture_disabled_by_default(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )
        session.messages = [
            {"role": "user", "content": "记住：以后始终先给结论"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "latest"},
        ]

        with (
            patch("ragtag_crew.agent.append_memory_note_if_missing") as mock_append,
            patch("ragtag_crew.agent.settings.session_summary_recent_messages", 1),
        ):
            session.compact(force=True)

        mock_append.assert_not_called()

    def test_precompact_memory_capture_writes_marker_messages(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )
        session.messages = [
            {"role": "user", "content": "记住：以后始终先给结论"},
            {"role": "assistant", "content": "收到"},
            {"role": "user", "content": "latest"},
        ]

        with (
            patch(
                "ragtag_crew.agent.append_memory_note_if_missing",
                return_value=(Path("inbox.md"), True),
            ) as mock_append,
            patch("ragtag_crew.agent.settings.auto_memory_precompact_enabled", True),
            patch("ragtag_crew.agent.settings.session_summary_recent_messages", 1),
        ):
            session.compact(force=True)

        mock_append.assert_called_once()
        note = mock_append.call_args[0][0]
        self.assertIn("[precompact/user]", note)
        self.assertIn("记住：以后始终先给结论", note)

    def test_precompact_memory_capture_ignores_non_marker_messages(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )
        session.messages = [
            {"role": "user", "content": "just a normal request"},
            {"role": "assistant", "content": "normal reply"},
            {"role": "user", "content": "latest"},
        ]

        with (
            patch("ragtag_crew.agent.append_memory_note_if_missing") as mock_append,
            patch("ragtag_crew.agent.settings.auto_memory_precompact_enabled", True),
            patch("ragtag_crew.agent.settings.session_summary_recent_messages", 1),
        ):
            session.compact(force=True)

        mock_append.assert_not_called()

    def test_detect_file_modifications_with_write(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )
        session.messages = [
            {"role": "user", "content": "old"},
            {
                "role": "tool",
                "tool_name": "write_file",
                "tool_call_id": "c1",
                "content": "ok",
            },
            {"role": "user", "content": "new"},
        ]
        self.assertTrue(session._detect_file_modifications(1))
        self.assertFalse(session._detect_file_modifications(2))

    def test_detect_file_modifications_no_write(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )
        session.messages = [
            {
                "role": "tool",
                "tool_name": "read_file",
                "tool_call_id": "c1",
                "content": "ok",
            },
            {
                "role": "tool",
                "tool_name": "grep",
                "tool_call_id": "c2",
                "content": "ok",
            },
        ]
        self.assertFalse(session._detect_file_modifications(0))

    def test_detect_file_modifications_edit_and_delete(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )
        session.messages = [
            {
                "role": "tool",
                "tool_name": "edit_file",
                "tool_call_id": "c1",
                "content": "ok",
            },
            {
                "role": "tool",
                "tool_name": "delete_file",
                "tool_call_id": "c2",
                "content": "ok",
            },
        ]
        self.assertTrue(session._detect_file_modifications(0))

    async def test_verify_phase_skipped_when_no_file_modification(self) -> None:
        call_count = 0

        async def ok_stream(**kwargs):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            return LLMResponse(content="done")

        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )

        with (
            patch("ragtag_crew.agent.stream_chat", side_effect=ok_stream),
            patch("ragtag_crew.agent.settings.verify_enabled", True),
        ):
            await session.prompt("just read something")

        self.assertEqual(call_count, 1)

    async def test_verify_phase_injected_after_file_write(self) -> None:
        call_count = 0

        async def write_then_done(**kwargs):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="write_file",
                            arguments={"path": "test.py", "content": "hello"},
                        )
                    ],
                )
            return LLMResponse(content="all good")

        async def noop_execute(**kwargs):  # type: ignore[no-untyped-def]
            return "file written"

        write_tool = Tool("write_file", "write", {"type": "object"}, noop_execute)

        with (
            patch("ragtag_crew.agent.get_tool", return_value=write_tool),
            patch("ragtag_crew.agent.stream_chat", side_effect=write_then_done),
            patch("ragtag_crew.agent.settings.verify_enabled", True),
            patch("ragtag_crew.agent.settings.verify_commands", "pytest"),
            patch("ragtag_crew.agent.settings.verify_max_turns", 2),
        ):
            session = AgentSession(
                model="openai/GLM-5.1",
                tools=[write_tool],
            )
            result = await session.prompt("add a test file")

        self.assertEqual(result, "all good")
        self.assertTrue(call_count >= 2)
        verify_msgs = [
            m for m in session.messages if "验证" in (m.get("content") or "")
        ]
        self.assertEqual(len(verify_msgs), 1)

    async def test_verify_phase_not_injected_when_disabled(self) -> None:
        call_count = 0

        async def write_then_done(**kwargs):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="write_file",
                            arguments={"path": "test.py", "content": "x"},
                        )
                    ],
                )
            return LLMResponse(content="ok")

        async def noop_execute(**kwargs):  # type: ignore[no-untyped-def]
            return "ok"

        write_tool = Tool("write_file", "write", {"type": "object"}, noop_execute)

        with (
            patch("ragtag_crew.agent.get_tool", return_value=write_tool),
            patch("ragtag_crew.agent.stream_chat", side_effect=write_then_done),
            patch("ragtag_crew.agent.settings.verify_enabled", False),
        ):
            session = AgentSession(
                model="openai/GLM-5.1",
                tools=[write_tool],
            )
            result = await session.prompt("write something")

        self.assertEqual(result, "ok")
        self.assertEqual(call_count, 2)
        verify_msgs = [
            m for m in session.messages if "验证" in (m.get("content") or "")
        ]
        self.assertEqual(len(verify_msgs), 0)

    async def test_external_tool_result_gets_source_prefix(self) -> None:
        async def external_stream(**kwargs):  # type: ignore[no-untyped-def]
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(id="c1", name="web_search", arguments={"query": "abc"})
                ],
            )

        async def search_execute(**kwargs):  # type: ignore[no-untyped-def]
            return "URL: https://example.com/result"

        search_tool = Tool(
            "web_search",
            "search",
            {"type": "object"},
            search_execute,
            source_type="search",
            source_name="serper",
        )

        with (
            patch("ragtag_crew.agent.get_tool", return_value=search_tool),
            patch(
                "ragtag_crew.agent.stream_chat",
                side_effect=[await external_stream(), LLMResponse(content="done")],
            ),
        ):
            session = AgentSession(model="openai/GLM-5.1", tools=[search_tool])
            await session.prompt("search this")

        tool_messages = [m for m in session.messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertTrue(tool_messages[0]["content"].startswith("[来源: search/serper]"))

    async def test_builtin_tool_result_has_no_source_prefix(self) -> None:
        async def builtin_stream(**kwargs):  # type: ignore[no-untyped-def]
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(id="c1", name="read", arguments={"path": "a.txt"})
                ],
            )

        async def read_execute(**kwargs):  # type: ignore[no-untyped-def]
            return "plain content"

        read_tool = Tool(
            "read",
            "read",
            {"type": "object"},
            read_execute,
            source_type="builtin",
            source_name="builtin",
        )

        with (
            patch("ragtag_crew.agent.get_tool", return_value=read_tool),
            patch(
                "ragtag_crew.agent.stream_chat",
                side_effect=[await builtin_stream(), LLMResponse(content="done")],
            ),
        ):
            session = AgentSession(model="openai/GLM-5.1", tools=[read_tool])
            await session.prompt("read this")

        tool_messages = [m for m in session.messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0]["content"], "plain content")

    async def test_maybe_capture_external_memory_skips_builtin(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )
        builtin_tool = Tool(
            "read",
            "read",
            {"type": "object"},
            _noop_tool,
            source_type="builtin",
            source_name="builtin",
        )
        with (
            patch("ragtag_crew.agent.append_memory_note_if_missing") as mock_append,
            patch(
                "ragtag_crew.agent.settings.auto_memory_external_results_enabled", True
            ),
        ):
            await session._maybe_capture_external_memory(builtin_tool, "plain content")

        mock_append.assert_not_called()

    async def test_maybe_capture_external_memory_skips_when_no_refs(self) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )
        search_tool = Tool(
            "web_search",
            "search",
            {"type": "object"},
            _noop_tool,
            source_type="search",
            source_name="serper",
        )
        with (
            patch("ragtag_crew.agent.append_memory_note_if_missing") as mock_append,
            patch(
                "ragtag_crew.agent.settings.auto_memory_external_results_enabled", True
            ),
        ):
            await session._maybe_capture_external_memory(search_tool, "no refs here")

        mock_append.assert_not_called()

    async def test_maybe_capture_external_memory_appends_when_external_refs_exist(
        self,
    ) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )
        search_tool = Tool(
            "web_search",
            "search",
            {"type": "object"},
            _noop_tool,
            source_type="search",
            source_name="serper",
        )
        with (
            patch(
                "ragtag_crew.agent.append_memory_note_if_missing",
                return_value=(Path("inbox.md"), True),
            ) as mock_append,
            patch(
                "ragtag_crew.agent.settings.auto_memory_external_results_enabled", True
            ),
        ):
            await session._maybe_capture_external_memory(
                search_tool,
                "URL: https://example.com/doc useful result",
            )

        mock_append.assert_called_once()
        note = mock_append.call_args[0][0]
        self.assertIn("[search/serper]", note)
        self.assertIn("https://example.com/doc", note)

    async def test_maybe_capture_external_memory_respects_source_type_allowlist(
        self,
    ) -> None:
        session = AgentSession(
            model="openai/GLM-5.1",
            tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
        )
        browser_tool = Tool(
            "browser_open",
            "browser",
            {"type": "object"},
            _noop_tool,
            source_type="browser",
            source_name="browser",
        )
        with (
            patch("ragtag_crew.agent.append_memory_note_if_missing") as mock_append,
            patch(
                "ragtag_crew.agent.settings.auto_memory_external_results_enabled", True
            ),
            patch(
                "ragtag_crew.agent.settings.auto_memory_external_source_types",
                "search,openapi",
            ),
        ):
            await session._maybe_capture_external_memory(
                browser_tool,
                "https://example.com/page",
            )

        mock_append.assert_not_called()


if __name__ == "__main__":
    unittest.main()
