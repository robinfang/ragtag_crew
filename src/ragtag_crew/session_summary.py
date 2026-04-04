"""Utilities for compacting older session history into a short summary."""

from __future__ import annotations

from typing import Any


def compact_history(
    *,
    messages: list[dict[str, Any]],
    previous_summary: str,
    recent_message_count: int,
    max_chars: int,
) -> tuple[str, list[dict[str, Any]]]:
    keep_count = max(recent_message_count, 1)
    if len(messages) <= keep_count:
        return previous_summary.strip(), messages

    split_index = len(messages) - keep_count
    older_messages = messages[:split_index]
    recent_messages = messages[split_index:]
    summary = _merge_summary(previous_summary, older_messages, max_chars=max_chars)
    return summary, recent_messages


def _merge_summary(
    previous_summary: str,
    messages: list[dict[str, Any]],
    *,
    max_chars: int,
) -> str:
    parts: list[str] = []
    earlier = previous_summary.strip()
    if earlier:
        earlier = _clip(earlier, max_chars // 2)
        parts.append(f"Earlier summarized context:\n{earlier}")

    entries = [_summarize_message(message) for message in messages]
    entries = [entry for entry in entries if entry]
    if entries:
        parts.append("Recently compacted history:\n" + "\n".join(f"- {entry}" for entry in entries))

    return _clip("\n\n".join(parts).strip(), max_chars)


def _summarize_message(message: dict[str, Any]) -> str:
    role = message.get("role")
    if role == "user":
        content = _clip_text(message.get("content", ""))
        return f"User request: {content}" if content else ""

    if role == "assistant":
        parts: list[str] = []
        content = _clip_text(message.get("content", ""))
        if content:
            parts.append(f"Assistant response: {content}")

        tool_calls = message.get("tool_calls") or []
        tool_names = [_tool_name(tool_call) for tool_call in tool_calls]
        tool_names = [name for name in tool_names if name]
        if tool_names:
            parts.append(f"Assistant used tools: {', '.join(tool_names)}")

        return " | ".join(parts)

    if role == "tool":
        content = _clip_text(message.get("content", ""))
        if not content:
            return ""
        if content.startswith("ERROR:"):
            return f"Tool error: {content}"
        tool_call_id = message.get("tool_call_id")
        if tool_call_id:
            return f"Tool result ({tool_call_id}): {content}"
        return f"Tool result: {content}"

    return ""


def _tool_name(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function") or {}
    name = function.get("name")
    if isinstance(name, str):
        return name
    name = tool_call.get("name")
    return name if isinstance(name, str) else ""


def _clip_text(value: Any, limit: int = 220) -> str:
    if not isinstance(value, str):
        return ""
    return _clip(" ".join(value.split()), limit)


def _clip(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
