from __future__ import annotations

import asyncio
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


if __name__ == "__main__":
    unittest.main()
