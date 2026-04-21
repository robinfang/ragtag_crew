"""LLM call layer — thin wrapper around litellm.

Provides a single ``stream_chat()`` async generator that yields
text deltas and a final ``LLMResponse`` with any tool calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import litellm

from ragtag_crew.codex_auth import (
    codex_network_error,
    codex_request_kwargs,
    codex_target_label,
    codex_timeout_value,
    codex_transport_description,
    ensure_codex_auth_state,
)
from ragtag_crew.config import settings
from ragtag_crew.errors import LLMChunkTimeoutError, LLMTimeoutError

# Silence litellm's noisy startup logs.
litellm.suppress_debug_info = True
_logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """A single tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Accumulated result of a streaming LLM call."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


_CODEX_OAUTH_MODELS = frozenset({"gpt-5.4"})
_DEFAULT_CODEX_INSTRUCTIONS = "You are a helpful assistant."


def _codex_model_name(model: str) -> str:
    return model.split("/", 1)[1] if "/" in model else model


def _should_use_codex_route(model: str) -> bool:
    auth_mode = settings.openai_auth_mode.strip().lower()
    if auth_mode != "codex":
        return False
    if not model.lower().startswith("openai/"):
        return False
    return _codex_model_name(model) in _CODEX_OAUTH_MODELS


def _stringify_message_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)


def _build_codex_instructions(messages: list[dict[str, Any]]) -> str:
    instructions = [
        _stringify_message_content(message.get("content")).strip()
        for message in messages
        if message.get("role") == "system"
    ]
    joined = "\n".join(part for part in instructions if part)
    return joined or _DEFAULT_CODEX_INSTRUCTIONS


def _build_codex_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = _stringify_message_content(message.get("content"))

        if role == "system":
            continue

        if role == "user":
            items.append(
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": content}],
                }
            )
            continue

        if role == "assistant":
            if content:
                items.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": content}],
                    }
                )
            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function") or {}
                arguments = function.get("arguments", "{}")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, ensure_ascii=False)
                items.append(
                    {
                        "type": "function_call",
                        "call_id": tool_call.get("id") or "",
                        "name": function.get("name") or "",
                        "arguments": arguments,
                    }
                )
            continue

        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id") or "",
                    "output": content,
                }
            )

    return items


def _build_codex_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not tools:
        return []

    converted: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            fn = tool["function"]
            converted.append(
                {
                    "type": "function",
                    "name": fn.get("name") or "",
                    "description": fn.get("description"),
                    "parameters": fn.get("parameters")
                    or {"type": "object", "properties": {}},
                    "strict": fn.get("strict"),
                }
            )
            continue

        if tool.get("type") == "function" and isinstance(tool.get("name"), str):
            converted.append(tool)

    return converted


def _remaining_llm_timeout(started_at: float, on_delta_total: float) -> float:
    return settings.llm_timeout - (time.monotonic() - started_at - on_delta_total)


async def _read_sse_event(stream: aiohttp.StreamReader, timeout: float) -> str | None:
    data_lines: list[str] = []
    while True:
        line = await asyncio.wait_for(stream.readline(), timeout=timeout)
        if not line:
            if data_lines:
                return "\n".join(data_lines)
            return None

        decoded = line.decode("utf-8").strip()
        if not decoded:
            if data_lines:
                return "\n".join(data_lines)
            continue
        if decoded.startswith(":"):
            continue
        if decoded.startswith("data:"):
            data_lines.append(decoded[5:].lstrip())


async def _raise_codex_http_error(response: aiohttp.ClientResponse) -> None:
    detail = (await response.text()).strip()
    if response.status == 401:
        raise RuntimeError(
            "Codex 请求失败：OpenCode 的 OpenAI 登录态可能已失效，请重新执行 opencode auth login。"
        )
    if response.status == 403:
        raise RuntimeError(
            "Codex 请求失败：当前账号可能没有 GPT-5.4/Codex 权限，或订阅额度不可用。"
        )
    suffix = f" {detail}" if detail else ""
    raise RuntimeError(f"Codex 请求失败：HTTP {response.status}.{suffix}")


async def _stream_codex_chat(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    on_delta: Any | None = None,
    should_abort: Any | None = None,
) -> LLMResponse:
    result = LLMResponse()
    started_at = time.monotonic()
    on_delta_total = 0.0
    received_content = False
    received_tool_call = False
    tc_index_map: dict[int, dict[str, str]] = {}

    _logger.debug(
        "Using Codex route for %s via %s",
        model,
        codex_transport_description(),
    )
    async with aiohttp.ClientSession(
        trust_env=settings.codex_trust_env_proxy
    ) as session:
        remaining = _remaining_llm_timeout(started_at, on_delta_total)
        if remaining <= 0:
            raise LLMTimeoutError(settings.llm_timeout, partial_response=result)
        auth_state = await ensure_codex_auth_state(
            session=session,
            timeout_seconds=remaining,
        )
        payload: dict[str, Any] = {
            "model": _codex_model_name(model),
            "instructions": _build_codex_instructions(messages),
            "input": _build_codex_input(messages),
            "stream": True,
            "store": False,
        }
        converted_tools = _build_codex_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools

        headers = {
            "Authorization": f"Bearer {auth_state.access_token}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": "ragtag_crew",
        }
        if auth_state.account_id:
            headers["ChatGPT-Account-Id"] = auth_state.account_id

        remaining = _remaining_llm_timeout(started_at, on_delta_total)
        if remaining <= 0:
            raise LLMTimeoutError(settings.llm_timeout, partial_response=result)

        request_kwargs = codex_request_kwargs(
            total_timeout_seconds=None,
            connect_timeout_seconds=codex_timeout_value(
                settings.codex_connect_timeout,
                remaining,
            ),
            read_timeout_seconds=codex_timeout_value(
                settings.codex_read_timeout,
                remaining,
            ),
        )
        response_target = codex_target_label(settings.codex_api_endpoint)

        try:
            response_ctx = session.post(
                settings.codex_api_endpoint,
                json=payload,
                headers=headers,
                **request_kwargs,
            )
            async with response_ctx as response:
                if response.status >= 400:
                    await _raise_codex_http_error(response)

                while True:
                    if should_abort and should_abort():
                        response.close()
                        break

                    remaining = _remaining_llm_timeout(started_at, on_delta_total)
                    if remaining <= 0:
                        response.close()
                        raise LLMTimeoutError(
                            settings.llm_timeout, partial_response=result
                        )

                    in_transition = received_content and not received_tool_call
                    chunk_wait = remaining
                    if settings.llm_chunk_timeout > 0 and not in_transition:
                        chunk_wait = min(chunk_wait, settings.llm_chunk_timeout)

                    try:
                        event_data = await _read_sse_event(response.content, chunk_wait)
                    except asyncio.TimeoutError as exc:
                        response.close()
                        if (
                            settings.llm_chunk_timeout > 0
                            and chunk_wait == settings.llm_chunk_timeout
                        ):
                            raise LLMChunkTimeoutError(
                                settings.llm_chunk_timeout,
                                partial_response=result,
                            ) from exc
                        raise LLMTimeoutError(
                            settings.llm_timeout, partial_response=result
                        ) from exc
                    except aiohttp.ClientError as exc:
                        response.close()
                        raise codex_network_error(
                            exc,
                            action="读取 Codex 流式响应",
                            target=response_target,
                        ) from exc

                    if event_data is None or event_data == "[DONE]":
                        break

                    try:
                        chunk = json.loads(event_data)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError("Codex SSE 事件不是合法 JSON。") from exc
                    chunk_type = chunk.get("type")

                    if chunk_type == "error":
                        message = chunk.get("message") or "unknown error"
                        raise RuntimeError(f"Codex 返回错误事件：{message}")

                    if chunk_type == "response.output_text.delta":
                        delta_text = chunk.get("delta")
                        if isinstance(delta_text, str) and delta_text:
                            result.content += delta_text
                            received_content = True
                            if on_delta:
                                t0 = time.monotonic()
                                await on_delta(delta_text)
                                on_delta_total += time.monotonic() - t0
                        continue

                    if chunk_type == "response.output_item.added":
                        item = chunk.get("item")
                        if (
                            isinstance(item, dict)
                            and item.get("type") == "function_call"
                        ):
                            received_tool_call = True
                            idx = (
                                chunk.get("output_index")
                                if isinstance(chunk.get("output_index"), int)
                                else len(tc_index_map)
                            )
                            entry = tc_index_map.setdefault(
                                idx, {"id": "", "name": "", "args_json": ""}
                            )
                            call_id = item.get("call_id")
                            name = item.get("name")
                            arguments = item.get("arguments")
                            if isinstance(call_id, str) and call_id:
                                entry["id"] = call_id
                            if isinstance(name, str) and name:
                                entry["name"] = name
                            if (
                                isinstance(arguments, str)
                                and arguments
                                and not entry["args_json"]
                            ):
                                entry["args_json"] = arguments
                        continue

                    if chunk_type == "response.function_call_arguments.delta":
                        received_tool_call = True
                        idx = (
                            chunk.get("output_index")
                            if isinstance(chunk.get("output_index"), int)
                            else 0
                        )
                        entry = tc_index_map.setdefault(
                            idx, {"id": "", "name": "", "args_json": ""}
                        )
                        delta_text = chunk.get("delta")
                        if isinstance(delta_text, str) and delta_text:
                            entry["args_json"] += delta_text
                        continue

                    if chunk_type == "response.output_item.done":
                        item = chunk.get("item")
                        if (
                            isinstance(item, dict)
                            and item.get("type") == "function_call"
                        ):
                            received_tool_call = True
                            idx = (
                                chunk.get("output_index")
                                if isinstance(chunk.get("output_index"), int)
                                else len(tc_index_map)
                            )
                            entry = tc_index_map.setdefault(
                                idx, {"id": "", "name": "", "args_json": ""}
                            )
                            call_id = item.get("call_id")
                            name = item.get("name")
                            arguments = item.get("arguments")
                            if isinstance(call_id, str) and call_id:
                                entry["id"] = call_id
                            if isinstance(name, str) and name:
                                entry["name"] = name
                            if (
                                isinstance(arguments, str)
                                and arguments
                                and not entry["args_json"]
                            ):
                                entry["args_json"] = arguments

                    if should_abort and should_abort():
                        response.close()
                        break
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise codex_network_error(
                exc,
                action="连接 Codex 服务",
                target=response_target,
            ) from exc

    for idx in sorted(tc_index_map):
        entry = tc_index_map[idx]
        try:
            arguments = json.loads(entry["args_json"]) if entry["args_json"] else {}
        except json.JSONDecodeError:
            arguments = {"_raw": entry["args_json"]}
        result.tool_calls.append(
            ToolCall(id=entry["id"], name=entry["name"], arguments=arguments)
        )

    return result


def _completion_provider_options(model: str) -> dict[str, Any]:
    """Return provider-specific kwargs for litellm.acompletion()."""
    resolved_model, provider, _dynamic_api_key, _api_base = litellm.get_llm_provider(
        model=model
    )

    options: dict[str, Any] = {}
    upper_model = resolved_model.upper()

    if provider == "anthropic":
        if settings.anthropic_api_key:
            options["api_key"] = settings.anthropic_api_key
        return options

    if provider == "openai":
        if upper_model.startswith("GLM-"):
            if settings.glm_api_key:
                options["api_key"] = settings.glm_api_key
            if settings.glm_api_base:
                options["api_base"] = settings.glm_api_base
            return options

        if settings.openai_api_key:
            options["api_key"] = settings.openai_api_key
        if settings.openai_api_base:
            options["api_base"] = settings.openai_api_base

    return options


async def stream_chat(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    on_delta: Any | None = None,
    should_abort: Any | None = None,
) -> LLMResponse:
    """Call the LLM with streaming and return the full response.

    Parameters
    ----------
    model:
        litellm model string, e.g. ``"anthropic/claude-sonnet-4-20250514"``.
    messages:
        Conversation history in OpenAI message format.
    tools:
        Tool schemas (OpenAI function-calling format).  Pass ``None``
        or ``[]`` to disable tool use.
    on_delta:
        Optional async callback ``(delta_text: str) -> None`` invoked
        for each text chunk.
    should_abort:
        Optional callback checked between streamed chunks. If it returns
        true, the stream is closed early and the partial result is returned.

    Returns
    -------
    LLMResponse with accumulated content and any tool_calls.
    """
    if _should_use_codex_route(model):
        return await _stream_codex_chat(
            model=model,
            messages=messages,
            tools=tools,
            on_delta=on_delta,
            should_abort=should_abort,
        )

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    kwargs.update(_completion_provider_options(model))
    if tools:
        kwargs["tools"] = tools

    try:
        response = await asyncio.wait_for(
            litellm.acompletion(**kwargs),
            timeout=settings.llm_timeout,
        )
    except asyncio.TimeoutError:
        raise LLMTimeoutError(settings.llm_timeout) from None

    result = LLMResponse()
    started_at = time.monotonic()
    on_delta_total = 0.0
    # Accumulators for streaming tool-call deltas.
    tc_index_map: dict[int, dict[str, str]] = {}  # index -> {id, name, args_json}
    stream_iter = response.__aiter__()
    # 用于豁免 chunk_timeout 的状态追踪：
    # 收到 content 后等待 tool_calls 时，模型可能需要较长时间生成 tool_call JSON，
    # 此阶段服务端不发送任何保活字节，不能用 chunk_timeout 判断超时。
    received_content = False
    received_tool_call = False

    while True:
        if should_abort and should_abort():
            aclose = getattr(response, "aclose", None)
            if callable(aclose):
                await aclose()
            break

        elapsed = time.monotonic() - started_at - on_delta_total
        remaining = settings.llm_timeout - elapsed
        if remaining <= 0:
            aclose = getattr(response, "aclose", None)
            if callable(aclose):
                await aclose()
            raise LLMTimeoutError(settings.llm_timeout, partial_response=result)

        # content→tool_call 过渡期：模型已输出文本但尚未开始 tool_call 时，
        # 服务端可能静默数十秒（与 context 大小正相关）。此阶段跳过 chunk_timeout，
        # 只保留整体 llm_timeout 兜底，避免误杀正常推理。
        in_transition = received_content and not received_tool_call
        chunk_wait = remaining
        if settings.llm_chunk_timeout > 0 and not in_transition:
            chunk_wait = min(chunk_wait, settings.llm_chunk_timeout)

        try:
            chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=chunk_wait)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError as exc:
            aclose = getattr(response, "aclose", None)
            if callable(aclose):
                await aclose()
            if (
                settings.llm_chunk_timeout > 0
                and chunk_wait == settings.llm_chunk_timeout
            ):
                raise LLMChunkTimeoutError(
                    settings.llm_chunk_timeout, partial_response=result
                ) from exc
            raise LLMTimeoutError(
                settings.llm_timeout, partial_response=result
            ) from exc

        delta = chunk.choices[0].delta

        # --- text content ---
        if delta.content:
            result.content += delta.content
            received_content = True
            if on_delta:
                t0 = time.monotonic()
                await on_delta(delta.content)
                on_delta_total += time.monotonic() - t0

        # --- tool calls (streamed in fragments) ---
        if delta.tool_calls:
            received_tool_call = True
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index if tc_delta.index is not None else 0
                if idx not in tc_index_map:
                    tc_index_map[idx] = {"id": "", "name": "", "args_json": ""}
                entry = tc_index_map[idx]
                if tc_delta.id:
                    entry["id"] = tc_delta.id
                if tc_delta.function and tc_delta.function.name:
                    entry["name"] = tc_delta.function.name
                if tc_delta.function and tc_delta.function.arguments:
                    entry["args_json"] += tc_delta.function.arguments

        if should_abort and should_abort():
            aclose = getattr(response, "aclose", None)
            if callable(aclose):
                await aclose()
            break

    # Assemble tool calls from accumulated fragments.
    for _idx in sorted(tc_index_map):
        entry = tc_index_map[_idx]
        try:
            args = json.loads(entry["args_json"]) if entry["args_json"] else {}
        except json.JSONDecodeError:
            args = {"_raw": entry["args_json"]}
        result.tool_calls.append(
            ToolCall(id=entry["id"], name=entry["name"], arguments=args)
        )

    return result
