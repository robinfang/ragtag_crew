"""Render Markdown-like assistant output into Telegram-safe HTML."""

from __future__ import annotations

import html
import re

_CODE_BLOCK_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")


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
    escaped = _convert_markdown_tables(escaped)
    escaped = _CODE_BLOCK_RE.sub(_stash_code, escaped)
    escaped = html.escape(escaped)
    escaped = _LINK_RE.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
        escaped,
    )
    escaped = _INLINE_CODE_RE.sub(
        lambda m: f"<code>{html.escape(m.group(1))}</code>", escaped
    )
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


def _convert_markdown_tables(text: str) -> str:
    lines = text.split("\n")
    result: list[str] = []
    table_lines: list[str] = []
    in_table = False

    for line in lines:
        if _TABLE_ROW_RE.match(line):
            table_lines.append(line)
            in_table = True
        elif in_table and _TABLE_SEP_RE.match(line):
            table_lines.append(line)
        else:
            if in_table:
                result.append(_render_table_as_code_block(table_lines))
                table_lines = []
                in_table = False
            result.append(line)

    if in_table and table_lines:
        result.append(_render_table_as_code_block(table_lines))

    return "\n".join(result)


def _render_table_as_code_block(table_lines: list[str]) -> str:
    rows: list[list[str]] = []
    for line in table_lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if any(c.replace("-", "").replace(":", "") == "" for c in cells):
            continue
        rows.append(cells)

    if not rows:
        return "\n".join(table_lines)

    col_widths = [0] * max(len(r) for r in rows)
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    formatted: list[str] = []
    for row_idx, row in enumerate(rows):
        padded = [
            cell.ljust(col_widths[i]) if i < len(col_widths) else cell
            for i, cell in enumerate(row)
        ]
        formatted.append(" | ".join(padded))
        if row_idx == 0:
            formatted.append("-+-".join("-" * w for w in col_widths))

    return "```\n" + "\n".join(formatted) + "\n```"
