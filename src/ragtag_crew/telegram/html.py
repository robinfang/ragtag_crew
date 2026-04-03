"""Render Markdown-like assistant output into Telegram-safe HTML."""

from __future__ import annotations

import html
import re

_CODE_BLOCK_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def render_telegram_html(text: str) -> str:
    """Convert a subset of Markdown into Telegram HTML."""
    normalized = text.replace("\r\n", "\n")
    code_blocks: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        language = match.group(1).strip()
        body = html.escape(match.group(2).strip("\n"))
        block = f"<pre><code>{body}</code></pre>"
        if language:
            header = html.escape(language)
            block = f"<blockquote>{header}</blockquote>\n{block}"
        code_blocks.append(block)
        return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00"

    escaped = _CODE_BLOCK_RE.sub(_stash_code, normalized)
    escaped = html.escape(escaped)
    escaped = _LINK_RE.sub(lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', escaped)
    escaped = _INLINE_CODE_RE.sub(lambda m: f"<code>{html.escape(m.group(1))}</code>", escaped)
    escaped = _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", escaped)
    escaped = _ITALIC_RE.sub(lambda m: f"<i>{m.group(1)}</i>", escaped)

    lines = []
    for raw_line in escaped.split("\n"):
        line = raw_line
        stripped = line.lstrip()

        if stripped.startswith("# "):
            line = f"<b>{stripped[2:].strip()}</b>"
        elif stripped.startswith("## "):
            line = f"<b>{stripped[3:].strip()}</b>"
        elif stripped.startswith("> "):
            line = f"<blockquote>{stripped[2:].strip()}</blockquote>"
        elif stripped.startswith(("- ", "* ")):
            line = f"• {stripped[2:].strip()}"

        lines.append(line)

    rendered = "\n".join(lines)
    for idx, block in enumerate(code_blocks):
        rendered = rendered.replace(f"\x00CODEBLOCK{idx}\x00", block)

    return rendered or "..."
