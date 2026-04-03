"""LLM call layer — thin wrapper around litellm.

Provides a single ``stream_chat()`` async generator that yields
text deltas and a final ``LLMResponse`` with any tool calls.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import litellm

from ragtag_crew.config import settings
from ragtag_crew.errors import LLMChunkTimeoutError, LLMTimeoutError

# Silence litellm's noisy startup logs.
litellm.suppress_debug_info = True


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


def _completion_provider_options(model: str) -> dict[str, Any]:
    """Return provider-specific kwargs for litellm.acompletion()."""
    resolved_model, provider, _dynamic_api_key, _api_base = litellm.get_llm_provider(model=model)

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
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    kwargs.update(_completion_provider_options(model))
    if tools:
        kwargs["tools"] = tools

    response = await litellm.acompletion(**kwargs)

    result = LLMResponse()
    started_at = time.monotonic()
    # Accumulators for streaming tool-call deltas.
    tc_index_map: dict[int, dict[str, str]] = {}  # index -> {id, name, args_json}
    stream_iter = response.__aiter__()

    while True:
        if should_abort and should_abort():
            aclose = getattr(response, "aclose", None)
            if callable(aclose):
                await aclose()
            break

        elapsed = time.monotonic() - started_at
        remaining = settings.llm_timeout - elapsed
        if remaining <= 0:
            aclose = getattr(response, "aclose", None)
            if callable(aclose):
                await aclose()
            raise LLMTimeoutError(settings.llm_timeout, partial_response=result)

        chunk_wait = remaining
        if settings.llm_chunk_timeout > 0:
            chunk_wait = min(chunk_wait, settings.llm_chunk_timeout)

        try:
            chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=chunk_wait)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError as exc:
            aclose = getattr(response, "aclose", None)
            if callable(aclose):
                await aclose()
            if settings.llm_chunk_timeout > 0 and chunk_wait == settings.llm_chunk_timeout:
                raise LLMChunkTimeoutError(settings.llm_chunk_timeout, partial_response=result) from exc
            raise LLMTimeoutError(settings.llm_timeout, partial_response=result) from exc

        delta = chunk.choices[0].delta

        # --- text content ---
        if delta.content:
            result.content += delta.content
            if on_delta:
                await on_delta(delta.content)

        # --- tool calls (streamed in fragments) ---
        if delta.tool_calls:
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
