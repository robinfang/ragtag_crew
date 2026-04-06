from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ragtag_crew.errors import LLMChunkTimeoutError, LLMTimeoutError
from ragtag_crew.llm import _completion_provider_options, stream_chat


class _FakeStream:
    def __init__(self, chunks, delays=None):  # type: ignore[no-untyped-def]
        self._chunks = list(chunks)
        self._delays = list(delays or [0.0] * len(self._chunks))
        self._index = 0
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        delay = self._delays[self._index]
        if delay:
            import asyncio

            await asyncio.sleep(delay)
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk

    async def aclose(self):
        self.closed = True


def _chunk(content: str):
    delta = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


class CompletionProviderOptionsTests(unittest.TestCase):
    def test_glm_model_uses_glm_key_and_coding_endpoint(self) -> None:
        with patch("ragtag_crew.llm.settings.glm_api_key", "glm-key"), patch(
            "ragtag_crew.llm.settings.glm_api_base",
            "https://open.bigmodel.cn/api/coding/paas/v4",
        ):
            options = _completion_provider_options("openai/GLM-5.1")

        self.assertEqual(
            options,
            {
                "api_key": "glm-key",
                "api_base": "https://open.bigmodel.cn/api/coding/paas/v4",
            },
        )

    def test_plain_openai_model_uses_openai_credentials(self) -> None:
        with patch("ragtag_crew.llm.settings.openai_api_key", "openai-key"), patch(
            "ragtag_crew.llm.settings.openai_api_base",
            "https://example.com/v1",
        ):
            options = _completion_provider_options("openai/gpt-4.1")

        self.assertEqual(
            options,
            {
                "api_key": "openai-key",
                "api_base": "https://example.com/v1",
            },
        )


class StreamChatTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_ttfb_timeout_raises_without_partial(self) -> None:
        async def _hang(**_kwargs):  # type: ignore[no-untyped-def]
            import asyncio
            await asyncio.sleep(999)

        with patch("ragtag_crew.llm.settings.llm_timeout", 0.01), patch(
            "ragtag_crew.llm.litellm.acompletion", side_effect=_hang
        ):
            with self.assertRaises(LLMTimeoutError) as ctx:
                await stream_chat(
                    model="openai/GLM-5.1",
                    messages=[{"role": "user", "content": "hi"}],
                )

        self.assertIsNone(ctx.exception.partial_response)

    async def test_chunk_timeout_keeps_partial_content(self) -> None:
        fake_stream = _FakeStream([_chunk("hello")], delays=[0.05])

        with patch("ragtag_crew.llm.settings.llm_timeout", 1), patch(
            "ragtag_crew.llm.settings.llm_chunk_timeout", 0.01
        ), patch("ragtag_crew.llm.litellm.acompletion", return_value=fake_stream):
            with self.assertRaises(LLMChunkTimeoutError) as ctx:
                await stream_chat(
                    model="openai/GLM-5.1",
                    messages=[{"role": "user", "content": "hi"}],
                )

        self.assertEqual(ctx.exception.partial_response.content, "")

    async def test_total_timeout_keeps_received_partial_content(self) -> None:
        fake_stream = _FakeStream([_chunk("hello"), _chunk(" world")], delays=[0.0, 0.05])

        with patch("ragtag_crew.llm.settings.llm_timeout", 0.01), patch(
            "ragtag_crew.llm.settings.llm_chunk_timeout", 0
        ), patch("ragtag_crew.llm.litellm.acompletion", return_value=fake_stream):
            with self.assertRaises(LLMTimeoutError) as ctx:
                await stream_chat(
                    model="openai/GLM-5.1",
                    messages=[{"role": "user", "content": "hi"}],
                )

        self.assertEqual(ctx.exception.partial_response.content, "hello")


if __name__ == "__main__":
    unittest.main()
