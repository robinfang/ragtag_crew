"""Utilities for compacting older session history into a short summary."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

_SUMMARY_TEXT_LIMIT = 500
_TOOL_ARG_KEYS = ("path", "file", "file_path", "query", "pattern", "url", "search")
_STALE_TRUNCATE_LIMIT = 200
_URL_RE = re.compile(r"https?://[^\s\]\)>,;]+")
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s\]\)>,;]+")
_UNIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9._-])/(?:[^\s\]\)>,;:]+/?)+")


@dataclass(frozen=True)
class CompressionBlock:
    block_id: str
    created_at: float
    message_count: int
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def clear_stale_tool_results(
    messages: list[dict[str, Any]],
    *,
    keep_recent: int = 8,
    truncate_to: int = _STALE_TRUNCATE_LIMIT,
) -> list[dict[str, Any]]:
    result = list(messages)
    tool_indices: list[int] = [
        i for i, m in enumerate(result) if m.get("role") == "tool"
    ]
    if keep_recent <= 0:
        stale_indices = tool_indices
    elif len(tool_indices) <= keep_recent:
        return result
    else:
        stale_indices = tool_indices[:-keep_recent]
    for idx in stale_indices:
        content = result[idx].get("content", "")
        if isinstance(content, str) and len(content) > truncate_to:
            placeholder = _clip(" ".join(content.split()), truncate_to)
            result[idx] = {
                **result[idx],
                "content": f"[Result truncated: {len(content)} chars] {placeholder}",
            }
    return result


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


def build_compression_block(
    messages: list[dict[str, Any]],
    *,
    max_chars: int = 1200,
) -> CompressionBlock | None:
    if not messages:
        return None

    entries = [_summarize_message(message) for message in messages]
    entries = [entry for entry in entries if entry]
    if not entries:
        return None

    summary = _clip("\n".join(f"- {entry}" for entry in entries), max_chars)
    return CompressionBlock(
        block_id=f"block-{int(time.time() * 1000)}",
        created_at=time.time(),
        message_count=len(messages),
        summary=summary,
    )


def render_compression_blocks(
    blocks: list[dict[str, Any]] | list[CompressionBlock],
    *,
    max_blocks: int = 6,
    max_chars: int = 2000,
) -> str:
    if not blocks:
        return ""

    normalized: list[CompressionBlock] = []
    for block in blocks[-max_blocks:]:
        if isinstance(block, CompressionBlock):
            normalized.append(block)
            continue
        try:
            normalized.append(
                CompressionBlock(
                    block_id=str(block.get("block_id", "")),
                    created_at=float(block.get("created_at", 0)),
                    message_count=int(block.get("message_count", 0)),
                    summary=str(block.get("summary", "")).strip(),
                )
            )
        except Exception:
            continue

    if not normalized:
        return ""

    lines: list[str] = []
    for index, block in enumerate(normalized, start=1):
        lines.append(
            f"- Block {index} ({block.message_count} msgs, id={block.block_id}): {block.summary}"
        )
    return _clip("\n".join(lines), max_chars)


def _merge_summary(
    previous_summary: str,
    messages: list[dict[str, Any]],
    *,
    max_chars: int,
) -> str:
    entries = [_summarize_message(message) for message in messages]
    entries = [entry for entry in entries if entry]

    parts: list[str] = []
    earlier = previous_summary.strip()
    if earlier:
        parts.append(f"Earlier summarized context:\n{earlier}")

    if entries:
        parts.append(
            "Recently compacted history:\n"
            + "\n".join(f"- {entry}" for entry in entries)
        )

    combined = "\n\n".join(parts).strip()
    if len(combined) <= max_chars:
        return combined

    combined = "\n\n".join(parts).strip()
    if not entries:
        return _clip(combined, max_chars)

    new_part = parts[-1]
    budget_for_older = max_chars - len(new_part) - 4
    if budget_for_older > 0:
        older_text = _clip("\n\n".join(parts[:-1]).strip(), budget_for_older)
        combined = f"{older_text}\n\n{new_part}"
    else:
        combined = new_part

    return _clip(combined, max_chars)


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
        if tool_calls:
            tool_labels = [_tool_call_label(tc) for tc in tool_calls]
            tool_labels = [lbl for lbl in tool_labels if lbl]
            if tool_labels:
                parts.append("Assistant used tools: " + " → ".join(tool_labels))

        return " | ".join(parts)

    if role == "tool":
        content = _clip_text(message.get("content", ""))
        if not content:
            return ""
        tool_name = _clip_text(message.get("tool_name", ""), limit=80)
        tool_source_type = _clip_text(message.get("tool_source_type", ""), limit=40)
        tool_source_name = _clip_text(message.get("tool_source_name", ""), limit=80)
        tool_label = _tool_label(tool_name, tool_source_type, tool_source_name)
        external_refs = _extract_external_refs(message.get("content", ""))
        if content.startswith("ERROR:"):
            prefix = f"Tool error ({tool_label})" if tool_label else "Tool error"
            return f"{prefix}: {content}"
        tool_call_id = message.get("tool_call_id")
        if tool_call_id:
            prefix = f"Tool result ({tool_call_id})"
        else:
            prefix = "Tool result"
        if tool_label:
            prefix += f" [{tool_label}]"
        if external_refs:
            return f"{prefix}: {content} | External refs: {external_refs}"
        return f"{prefix}: {content}"

    return ""


def _tool_call_label(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function") or {}
    name = function.get("name")
    if not isinstance(name, str):
        name = tool_call.get("name")
    if not isinstance(name, str):
        return ""
    args_str = function.get("arguments", "") or ""
    args: dict[str, Any] = {}
    if isinstance(args_str, str):
        try:
            args = json.loads(args_str)
        except (json.JSONDecodeError, ValueError):
            args = {}
    elif isinstance(args_str, dict):
        args = args_str
    highlights = [
        f"{k}={_clip_text(str(args[k]), limit=60)}" for k in _TOOL_ARG_KEYS if k in args
    ]
    if highlights:
        return f"{name}({', '.join(highlights)})"
    return name


def _tool_name(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function") or {}
    name = function.get("name")
    if isinstance(name, str):
        return name
    name = tool_call.get("name")
    return name if isinstance(name, str) else ""


def _tool_label(tool_name: str, source_type: str, source_name: str) -> str:
    parts = [part for part in [tool_name, source_type, source_name] if part]
    return "/".join(parts)


def _extract_external_refs(value: Any, limit: int = 5) -> str:
    if not isinstance(value, str) or not value:
        return ""
    refs: list[str] = []
    for pattern in (_URL_RE, _WINDOWS_PATH_RE, _UNIX_PATH_RE):
        for match in pattern.finditer(value):
            cleaned = match.group(0).rstrip(",.;)]}\"'")
            if cleaned not in refs:
                refs.append(cleaned)
            if len(refs) >= limit:
                return ", ".join(refs)
    return ", ".join(refs)


def _clip_text(value: Any, limit: int = _SUMMARY_TEXT_LIMIT) -> str:
    if not isinstance(value, str):
        return ""
    return _clip(" ".join(value.split()), limit)


def _clip(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
