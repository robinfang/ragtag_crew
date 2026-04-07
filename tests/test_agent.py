from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

from ragtag_crew.agent import AgentSession
from ragtag_crew.errors import LLMChunkTimeoutError, TurnTimeoutError
from ragtag_crew.llm import LLMResponse
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

        with patch("ragtag_crew.agent.settings.turn_timeout", 0.01), patch(
            "ragtag_crew.agent.stream_chat",
            side_effect=slow_stream_chat,
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
            raise LLMChunkTimeoutError(30, partial_response=LLMResponse(content="hello"))

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

        with patch("ragtag_crew.agent.stream_chat", side_effect=ok_stream), patch(
            "ragtag_crew.agent.settings.session_summary_trigger_messages", 4
        ), patch("ragtag_crew.agent.settings.session_summary_recent_messages", 2):
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

        with patch("ragtag_crew.agent.stream_chat", side_effect=ok_stream), patch(
            "ragtag_crew.agent.settings.session_summary_trigger_messages", 3
        ), patch("ragtag_crew.agent.settings.session_summary_recent_messages", 2):
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

        with patch("ragtag_crew.agent.settings.session_summary_trigger_messages", 10), patch(
            "ragtag_crew.agent.settings.session_summary_recent_messages", 2
        ):
            changed = session.compact(force=True)

        self.assertTrue(changed)
        self.assertEqual(len(session.messages), 2)
        self.assertIn("first", session.session_summary)


if __name__ == "__main__":
    unittest.main()
