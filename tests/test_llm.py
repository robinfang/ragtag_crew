from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ragtag_crew.errors import LLMChunkTimeoutError, LLMTimeoutError
from ragtag_crew.llm import (
    _build_codex_instructions,
    _build_codex_input,
    _completion_provider_options,
    stream_chat,
)


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


class _FakeContent:
    def __init__(self, lines):  # type: ignore[no-untyped-def]
        self._lines = [
            line if isinstance(line, bytes) else line.encode("utf-8") for line in lines
        ]
        self._index = 0

    async def readline(self):
        if self._index >= len(self._lines):
            return b""
        line = self._lines[self._index]
        self._index += 1
        return line


class _FakeResponse:
    def __init__(self, lines, status: int = 200):  # type: ignore[no-untyped-def]
        self.content = _FakeContent(lines)
        self.status = status
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return ""

    def close(self):
        self.closed = True


class _FakeClientSession:
    def __init__(self, response, **session_kwargs):
        self._response = response
        self.calls = []
        self.session_kwargs = session_kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((url, kwargs))
        return self._response


def _sse_event(payload: dict[str, object]) -> list[str]:
    return [f"data: {json.dumps(payload, separators=(',', ':'))}\n", "\n"]


class CompletionProviderOptionsTests(unittest.TestCase):
    def test_glm_model_uses_glm_key_and_coding_endpoint(self) -> None:
        with (
            patch("ragtag_crew.llm.settings.glm_api_key", "glm-key"),
            patch(
                "ragtag_crew.llm.settings.glm_api_base",
                "https://open.bigmodel.cn/api/coding/paas/v4",
            ),
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
        with (
            patch("ragtag_crew.llm.settings.openai_api_key", "openai-key"),
            patch(
                "ragtag_crew.llm.settings.openai_api_base",
                "https://example.com/v1",
            ),
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

        with (
            patch("ragtag_crew.llm.settings.llm_timeout", 0.01),
            patch("ragtag_crew.llm.litellm.acompletion", side_effect=_hang),
        ):
            with self.assertRaises(LLMTimeoutError) as ctx:
                await stream_chat(
                    model="openai/GLM-5.1",
                    messages=[{"role": "user", "content": "hi"}],
                )

        self.assertIsNone(ctx.exception.partial_response)

    async def test_chunk_timeout_keeps_partial_content(self) -> None:
        fake_stream = _FakeStream([_chunk("hello")], delays=[0.05])

        with (
            patch("ragtag_crew.llm.settings.llm_timeout", 1),
            patch("ragtag_crew.llm.settings.llm_chunk_timeout", 0.01),
            patch("ragtag_crew.llm.litellm.acompletion", return_value=fake_stream),
        ):
            with self.assertRaises(LLMChunkTimeoutError) as ctx:
                await stream_chat(
                    model="openai/GLM-5.1",
                    messages=[{"role": "user", "content": "hi"}],
                )

        self.assertEqual(ctx.exception.partial_response.content, "")

    async def test_total_timeout_keeps_received_partial_content(self) -> None:
        fake_stream = _FakeStream(
            [_chunk("hello"), _chunk(" world")], delays=[0.0, 0.05]
        )

        with (
            patch("ragtag_crew.llm.settings.llm_timeout", 0.01),
            patch("ragtag_crew.llm.settings.llm_chunk_timeout", 0),
            patch("ragtag_crew.llm.litellm.acompletion", return_value=fake_stream),
        ):
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

        with (
            patch("ragtag_crew.llm.settings.llm_timeout", 2),
            patch("ragtag_crew.llm.settings.llm_chunk_timeout", 0.01),
            patch("ragtag_crew.llm.litellm.acompletion", return_value=fake_stream),
        ):
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

        with (
            patch("ragtag_crew.llm.settings.llm_timeout", 0.05),
            patch("ragtag_crew.llm.settings.llm_chunk_timeout", 0),
            patch("ragtag_crew.llm.litellm.acompletion", return_value=fake_stream),
        ):
            result = await stream_chat(
                model="openai/GLM-5.1",
                messages=[{"role": "user", "content": "hi"}],
                on_delta=_slow_delta,
            )

        self.assertEqual(result.content, "hello")


class CodexInputMappingTests(unittest.TestCase):
    def test_build_codex_instructions_joins_system_messages(self) -> None:
        messages = [
            {"role": "system", "content": "sys one"},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "sys two"},
        ]

        result = _build_codex_instructions(messages)

        self.assertEqual(result, "sys one\nsys two")

    def test_build_codex_input_preserves_tool_history(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "calling tool",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "README.md"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "tool_name": "read_file",
                "content": "# title",
            },
        ]

        result = _build_codex_input(messages)

        self.assertEqual(
            result[0],
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        )
        self.assertEqual(
            result[1],
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "calling tool"}],
            },
        )
        self.assertEqual(
            result[2],
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "read_file",
                "arguments": '{"path": "README.md"}',
            },
        )
        self.assertEqual(
            result[3],
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "# title",
            },
        )


class CodexRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_gpt_5_4_uses_codex_route_when_enabled(self) -> None:
        fake_response = _FakeResponse(
            [
                *_sse_event(
                    {
                        "type": "response.output_text.delta",
                        "item_id": "msg_1",
                        "delta": "Hel",
                    }
                ),
                *_sse_event(
                    {
                        "type": "response.output_text.delta",
                        "item_id": "msg_1",
                        "delta": "lo",
                    }
                ),
                *_sse_event(
                    {
                        "type": "response.output_item.added",
                        "output_index": 0,
                        "item": {
                            "type": "function_call",
                            "id": "fc_1",
                            "call_id": "call_1",
                            "name": "read_file",
                            "arguments": "",
                        },
                    }
                ),
                *_sse_event(
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": "fc_1",
                        "output_index": 0,
                        "delta": '{"path": "REA',
                    }
                ),
                *_sse_event(
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": "fc_1",
                        "output_index": 0,
                        "delta": 'DME.md"}',
                    }
                ),
                *_sse_event(
                    {
                        "type": "response.completed",
                        "response": {
                            "usage": {
                                "input_tokens": 1,
                                "output_tokens": 1,
                                "output_tokens_details": {},
                                "input_tokens_details": {},
                            }
                        },
                    }
                ),
                "data: [DONE]\n",
                "\n",
            ]
        )
        created_sessions: list[_FakeClientSession] = []

        def _session_factory(**kwargs):  # type: ignore[no-untyped-def]
            session = _FakeClientSession(fake_response, **kwargs)
            created_sessions.append(session)
            return session

        async def _on_delta(_text: str) -> None:
            return None

        with (
            patch(
                "ragtag_crew.llm.aiohttp.ClientSession", side_effect=_session_factory
            ),
            patch(
                "ragtag_crew.llm.ensure_codex_auth_state",
                return_value=SimpleNamespace(access_token="token", account_id="acct_1"),
            ),
            patch("ragtag_crew.llm.settings.openai_auth_mode", "codex"),
        ):
            result = await stream_chat(
                model="openai/gpt-5.4",
                messages=[
                    {"role": "system", "content": "Be precise."},
                    {"role": "user", "content": "hi"},
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "description": "read file",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                on_delta=_on_delta,
            )

        fake_session = created_sessions[0]

        self.assertEqual(result.content, "Hello")
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].id, "call_1")
        self.assertEqual(result.tool_calls[0].name, "read_file")
        self.assertEqual(result.tool_calls[0].arguments, {"path": "README.md"})
        self.assertEqual(
            fake_session.calls[0][0], "https://chatgpt.com/backend-api/codex/responses"
        )
        self.assertEqual(
            fake_session.calls[0][1]["headers"]["ChatGPT-Account-Id"],
            "acct_1",
        )
        self.assertEqual(
            fake_session.calls[0][1]["json"]["tools"][0]["name"],
            "read_file",
        )
        self.assertEqual(
            fake_session.calls[0][1]["json"]["instructions"],
            "Be precise.",
        )
        self.assertEqual(
            fake_session.calls[0][1]["json"]["input"][0],
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        )
        self.assertFalse(fake_session.calls[0][1]["json"]["store"])
        self.assertTrue(fake_session.session_kwargs["trust_env"])

    async def test_codex_route_adds_default_instructions_without_system(self) -> None:
        fake_response = _FakeResponse(["data: [DONE]\n", "\n"])
        created_sessions: list[_FakeClientSession] = []

        def _session_factory(**kwargs):  # type: ignore[no-untyped-def]
            session = _FakeClientSession(fake_response, **kwargs)
            created_sessions.append(session)
            return session

        with (
            patch(
                "ragtag_crew.llm.aiohttp.ClientSession", side_effect=_session_factory
            ),
            patch(
                "ragtag_crew.llm.ensure_codex_auth_state",
                return_value=SimpleNamespace(access_token="token", account_id=None),
            ),
            patch("ragtag_crew.llm.settings.openai_auth_mode", "codex"),
        ):
            await stream_chat(
                model="openai/gpt-5.4",
                messages=[{"role": "user", "content": "hi"}],
            )

        fake_session = created_sessions[0]
        self.assertEqual(
            fake_session.calls[0][1]["json"]["instructions"],
            "You are a helpful assistant.",
        )

    async def test_codex_route_prefers_explicit_proxy(self) -> None:
        fake_response = _FakeResponse(["data: [DONE]\n", "\n"])
        created_sessions: list[_FakeClientSession] = []

        def _session_factory(**kwargs):  # type: ignore[no-untyped-def]
            session = _FakeClientSession(fake_response, **kwargs)
            created_sessions.append(session)
            return session

        with (
            patch(
                "ragtag_crew.llm.aiohttp.ClientSession", side_effect=_session_factory
            ),
            patch(
                "ragtag_crew.llm.ensure_codex_auth_state",
                return_value=SimpleNamespace(access_token="token", account_id=None),
            ),
            patch("ragtag_crew.llm.settings.openai_auth_mode", "codex"),
            patch("ragtag_crew.llm.settings.codex_proxy", "http://localhost:1087"),
        ):
            await stream_chat(
                model="openai/gpt-5.4",
                messages=[{"role": "user", "content": "hi"}],
            )

        fake_session = created_sessions[0]
        self.assertEqual(fake_session.calls[0][1]["proxy"], "http://localhost:1087")

    async def test_codex_route_reports_timeout_with_proxy_hint(self) -> None:
        class _TimeoutSession(_FakeClientSession):
            def post(self, url, **kwargs):  # type: ignore[no-untyped-def]
                raise asyncio.TimeoutError()

        with (
            patch(
                "ragtag_crew.llm.aiohttp.ClientSession",
                return_value=_TimeoutSession(_FakeResponse([])),
            ),
            patch(
                "ragtag_crew.llm.ensure_codex_auth_state",
                return_value=SimpleNamespace(access_token="token", account_id=None),
            ),
            patch("ragtag_crew.llm.settings.openai_auth_mode", "codex"),
            patch("ragtag_crew.llm.settings.codex_trust_env_proxy", True),
            patch("ragtag_crew.llm.settings.codex_proxy", ""),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await stream_chat(
                    model="openai/gpt-5.4",
                    messages=[{"role": "user", "content": "hi"}],
                )

        self.assertIn("连接 Codex 服务失败", str(ctx.exception))
        self.assertIn("环境代理", str(ctx.exception))

    async def test_non_codex_models_keep_litellm_route(self) -> None:
        fake_stream = _FakeStream([_chunk("hello")])

        with (
            patch("ragtag_crew.llm.settings.openai_auth_mode", "codex"),
            patch(
                "ragtag_crew.llm.litellm.acompletion", return_value=fake_stream
            ) as completion_mock,
        ):
            result = await stream_chat(
                model="openai/gpt-4.1",
                messages=[{"role": "user", "content": "hi"}],
            )

        self.assertEqual(result.content, "hello")
        completion_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
