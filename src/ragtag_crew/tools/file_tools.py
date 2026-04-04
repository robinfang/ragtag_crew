"""File tools: read, write, edit."""

from __future__ import annotations

from ragtag_crew.tools import Tool, register_tool
from ragtag_crew.tools.path_utils import resolve_path, resolve_read_path


# ---- read -----------------------------------------------------------------

async def _read_file(path: str, offset: int = 1, limit: int = 2000) -> str:
    resolved = resolve_read_path(path)
    if not resolved.is_file():
        return f"ERROR: file not found: {path}"
    try:
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
    except Exception as exc:
        return f"ERROR: {exc}"
    total = len(lines)
    start = max(offset - 1, 0)
    selected = lines[start : start + limit]
    numbered = "".join(
        f"{start + i + 1:6d}\t{line}" for i, line in enumerate(selected)
    )
    header = f"({total} lines total)\n" if total > limit else ""
    return header + numbered


read_tool = register_tool(
    Tool(
        name="read",
        description=(
            "Read a file's contents with line numbers. Use offset/limit for large files. "
            "Do NOT use `bash cat`/`head`/`tail` to read files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path (relative to working dir or absolute)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Start line number (1-based, default 1)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of lines to return (default 2000)",
                },
            },
            "required": ["path"],
        },
        execute=_read_file,
    )
)


# ---- write ----------------------------------------------------------------

async def _write_file(path: str, content: str) -> str:
    resolved = resolve_path(path)
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
    except Exception as exc:
        return f"ERROR: {exc}"
    return f"OK: wrote {len(content)} chars to {path}"


write_tool = register_tool(
    Tool(
        name="write",
        description=(
            "Create or overwrite a file. "
            "Do NOT use `bash echo`/`cat` with redirects to write files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path (relative to working dir or absolute)",
                },
                "content": {
                    "type": "string",
                    "description": "Full file content to write",
                },
            },
            "required": ["path", "content"],
        },
        execute=_write_file,
    )
)


# ---- edit -----------------------------------------------------------------

async def _edit_file(path: str, old_string: str, new_string: str) -> str:
    resolved = resolve_path(path)
    if not resolved.is_file():
        return f"ERROR: file not found: {path}"
    try:
        content = resolved.read_text(encoding="utf-8")
    except Exception as exc:
        return f"ERROR: {exc}"

    count = content.count(old_string)
    if count == 0:
        return f"ERROR: old_string not found in {path}"
    if count > 1:
        return (
            f"ERROR: old_string appears {count} times in {path}; "
            "must be unique.  Provide more surrounding context."
        )

    new_content = content.replace(old_string, new_string, 1)
    resolved.write_text(new_content, encoding="utf-8")
    return f"OK: replaced 1 occurrence in {path}"


edit_tool = register_tool(
    Tool(
        name="edit",
        description=(
            "Replace a unique string in a file. old_string must appear exactly once; "
            "include surrounding context to make it unique. "
            "Do NOT use `bash sed`/`awk` to edit files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to find (must be unique in the file)",
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
        execute=_edit_file,
    )
)
