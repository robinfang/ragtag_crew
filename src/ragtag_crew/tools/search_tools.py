"""Search tools: grep, find, ls."""

from __future__ import annotations

import asyncio
import fnmatch
import re
from pathlib import Path

from ragtag_crew.tools import Tool, register_tool
from ragtag_crew.tools.path_utils import display_path, resolve_path

_MAX_OUTPUT = 50_000
_SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules"}


def _should_skip(path: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts)


def _truncate(text: str) -> str:
    return text if len(text) <= _MAX_OUTPUT else text[:_MAX_OUTPUT] + "\n...[truncated]"


async def _grep_search(pattern: str, path: str = ".", include: str = "*") -> str:
    root = resolve_path(path)
    if not root.exists():
        return f"ERROR: path not found: {path}"

    rg_result = await _grep_with_rg(pattern=pattern, root=root, include=include)
    if rg_result is not None:
        return rg_result

    return await _grep_with_python(pattern=pattern, root=root, include=include)


async def _grep_with_rg(pattern: str, root: Path, include: str) -> str | None:
    command = ["rg", "--line-number", "--with-filename", pattern, str(root)]
    if include and include != "*":
        command[1:1] = ["--glob", include]

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None

    stdout, stderr = await proc.communicate()
    if proc.returncode not in (0, 1):
        error = stderr.decode(errors="replace").strip() or f"rg exited with code {proc.returncode}"
        return f"ERROR: {error}"

    output = stdout.decode(errors="replace").strip()
    return _truncate(output) if output else "No matches found."


async def _grep_with_python(pattern: str, root: Path, include: str) -> str:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"ERROR: invalid regex: {exc}"

    files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
    matches: list[str] = []

    for file_path in files:
        if _should_skip(file_path):
            continue
        if include and include != "*":
            rel = display_path(file_path)
            if not fnmatch.fnmatch(file_path.name, include) and not fnmatch.fnmatch(rel, include):
                continue
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue

        rel_path = display_path(file_path)
        for index, line in enumerate(lines, start=1):
            if regex.search(line):
                matches.append(f"{rel_path}:{index}: {line}")

    return _truncate("\n".join(matches)) if matches else "No matches found."


async def _find_files(pattern: str = "*", path: str = ".") -> str:
    root = resolve_path(path)
    if not root.exists():
        return f"ERROR: path not found: {path}"

    if root.is_file():
        matched = fnmatch.fnmatch(root.name, pattern) or fnmatch.fnmatch(display_path(root), pattern)
        return display_path(root) if matched else "No files found."

    matches: list[str] = []
    for file_path in root.rglob(pattern):
        if _should_skip(file_path):
            continue
        matches.append(display_path(file_path))

    matches.sort()
    return _truncate("\n".join(matches)) if matches else "No files found."


async def _list_dir(path: str = ".") -> str:
    target = resolve_path(path)
    if not target.exists():
        return f"ERROR: path not found: {path}"

    if target.is_file():
        return display_path(target)

    entries: list[str] = []
    for child in sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        if _should_skip(child):
            continue
        name = child.name + ("/" if child.is_dir() else "")
        entries.append(name)

    return "\n".join(entries) if entries else "(empty directory)"


grep_tool = register_tool(
    Tool(
        name="grep",
        description="Search file contents with a regex pattern.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file path", "default": "."},
                "include": {"type": "string", "description": "Glob filter such as *.py", "default": "*"},
            },
            "required": ["pattern"],
        },
        execute=_grep_search,
    )
)


find_tool = register_tool(
    Tool(
        name="find",
        description="Find files and directories by glob pattern.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern to match", "default": "*"},
                "path": {"type": "string", "description": "Directory to search in", "default": "."},
            },
        },
        execute=_find_files,
    )
)


ls_tool = register_tool(
    Tool(
        name="ls",
        description="List a directory's contents.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to list", "default": "."},
            },
        },
        execute=_list_dir,
    )
)
