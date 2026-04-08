"""Shared formatting helpers for external capability providers."""

from __future__ import annotations

from typing import Any

OUTPUT_LIMIT = 50_000


def truncate_output(text: str, limit: int = OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def clip_text(value: Any, limit: int = 240) -> str:
    text = value if isinstance(value, str) else ""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
