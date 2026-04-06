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


def _tool_chunk(name: str = "fn", args: str = "{}"):
    """Simulate a tool_call streaming chunk."""
    tc = SimpleNamespace(
        index=0,
        id="call_1",
        function=SimpleNamespace(name=name, arguments=args),
    )
    delta = SimpleNamespace(content=None, tool_calls=[tc])
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

    async def test_content_to_tool_call_transition_bypasses_chunk_timeout(self) -> None:
        """content→tool_call 过渡期不受 chunk_timeout 限制。

        场景：模型先输出 content 文本，然后静默一段时间（超过 chunk_timeout）
        后才开始 tool_call。预期：不触发 LLMChunkTimeoutError，正常返回。
        """
        # content chunk 立即返回，tool_call chunk 延迟超过 chunk_timeout
        fake_stream = _FakeStream(
            [_chunk("I will call the tool"), _tool_chunk("fn", '{"x": 1}')],
            delays=[0.0, 0.05],  # 0.05s > chunk_timeout=0.01s
        )

        with patch("ragtag_crew.llm.settings.llm_timeout", 2), patch(
            "ragtag_crew.llm.settings.llm_chunk_timeout", 0.01
        ), patch("ragtag_crew.llm.litellm.acompletion", return_value=fake_stream):
            result = await stream_chat(
                model="openai/GLM-5.1",
                messages=[{"role": "user", "content": "hi"}],
            )

        self.assertEqual(result.content, "I will call the tool")
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, "fn")


    async def test_on_delta_delay_not_counted_in_timeout(self) -> None:
        """on_delta 回调的等待时间不应计入 llm_timeout。"""
        async def _slow_delta(text: str) -> None:
            import asyncio
            await asyncio.sleep(0.1)

        # llm_timeout=0.05 < on_delta sleep 0.1，但不应超时
        fake_stream = _FakeStream([_chunk("hello")], delays=[0.0])

        with patch("ragtag_crew.llm.settings.llm_timeout", 0.05), patch(
            "ragtag_crew.llm.settings.llm_chunk_timeout", 0
        ), patch("ragtag_crew.llm.litellm.acompletion", return_value=fake_stream):
            result = await stream_chat(
                model="openai/GLM-5.1",
                messages=[{"role": "user", "content": "hi"}],
                on_delta=_slow_delta,
            )

        self.assertEqual(result.content, "hello")


if __name__ == "__main__":
    unittest.main()
