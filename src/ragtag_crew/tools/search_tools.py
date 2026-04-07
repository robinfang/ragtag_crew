"""Search tools: grep, find, ls.

Uses ripgrep (rg) for high-performance file and content search when available,
with Python fallback when rg is not installed.
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
import shutil
from pathlib import Path

from ragtag_crew.tools import Tool, register_tool
from ragtag_crew.tools.path_utils import display_path, resolve_read_path

_MAX_OUTPUT = 50_000
_SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules"}


def _should_skip(path: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts)


def _truncate(text: str) -> str:
    return text if len(text) <= _MAX_OUTPUT else text[:_MAX_OUTPUT] + "\n...[truncated]"


def _get_rg_path() -> str | None:
    from ragtag_crew.config import settings

    if settings.rg_command and settings.rg_command != "rg":
        configured = Path(settings.rg_command)
        if configured.is_file():
            return str(configured)

    which_result = shutil.which("rg")
    if which_result:
        return which_result

    from ragtag_crew.tools.bin_resolver import _cached_binary

    cached = _cached_binary("rg")
    if cached.is_file():
        return str(cached)

    return None


async def _grep_search(pattern: str, path: str = ".", include: str = "*", case_insensitive: bool = True) -> str:
    root = resolve_read_path(path)
    if not root.exists():
        return f"ERROR: path not found: {path}"

    rg_result = await _grep_with_rg(pattern=pattern, root=root, include=include, case_insensitive=case_insensitive)
    if rg_result is not None:
        return rg_result

    return await _grep_with_python(pattern=pattern, root=root, include=include, case_insensitive=case_insensitive)


async def _grep_with_rg(pattern: str, root: Path, include: str, case_insensitive: bool = True) -> str | None:
    rg_path = _get_rg_path()
    if rg_path is None:
        return None

    command = [rg_path, "--line-number", "--with-filename"]
    if case_insensitive:
        command.append("-i")
    if include and include != "*":
        command.extend(["--glob", include])
    command.extend([pattern, str(root)])

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return None

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return f"ERROR: ripgrep timed out after 30s."

    if proc.returncode not in (0, 1):
        error = stderr.decode(errors="replace").strip() or f"rg exited with code {proc.returncode}"
        return f"ERROR: {error}"

    output = stdout.decode(errors="replace").strip()
    return _truncate(output) if output else "No matches found."


async def _grep_with_python(pattern: str, root: Path, include: str, case_insensitive: bool = True) -> str:
    try:
        regex = re.compile(pattern, re.IGNORECASE if case_insensitive else 0)
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
    root = resolve_read_path(path)
    if not root.exists():
        return f"ERROR: path not found: {path}"

    rg_result = await _find_with_rg(pattern=pattern, root=root)
    if rg_result is not None:
        return rg_result

    return await _find_with_python(pattern=pattern, root=root)


async def _find_with_rg(pattern: str, root: Path) -> str | None:
    rg_path = _get_rg_path()
    if rg_path is None:
        return None

    command = [rg_path, "--files", "--sort", "path"]
    if pattern and pattern != "*":
        command.extend(["--glob", pattern])
    command.append(str(root))

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return None

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return f"ERROR: ripgrep timed out after 30s."

    if proc.returncode not in (0, 1):
        error = stderr.decode(errors="replace").strip() or f"rg exited with code {proc.returncode}"
        return f"ERROR: {error}"

    output = stdout.decode(errors="replace").strip()
    if not output:
        return "No files found."

    # rg 输出绝对路径，统一转换为相对于 root 的路径（正斜杠）
    lines: list[str] = []
    for line in output.splitlines():
        try:
            rel = Path(line).relative_to(root)
            lines.append(rel.as_posix())
        except ValueError:
            lines.append(line)
    return _truncate("\n".join(lines))


async def _find_with_python(pattern: str, root: Path) -> str:
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
    target = resolve_read_path(path)
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
        description=(
            "Search file contents using regex. Use this instead of `bash grep`. "
            "Supports ripgrep internally for fast search. "
            "Returns file paths with line numbers and matching lines."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file path (relative to working dir or absolute)", "default": "."},
                "include": {"type": "string", "description": "File filter, e.g. *.py, *.{ts,tsx}", "default": "*"},
                "case_insensitive": {"type": "boolean", "description": "Case-insensitive search (-i flag)", "default": True},
            },
            "required": ["pattern"],
        },
        execute=_grep_search,
    )
)


find_tool = register_tool(
    Tool(
        name="find",
        description=(
            "Find files and directories by name pattern. Use this instead of `bash find`/`ls`/`fd`. "
            "Supports ripgrep internally for fast search. "
            "Returns matching file paths sorted alphabetically."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. *.py, **/*.ts, src/**/*.json", "default": "*"},
                "path": {"type": "string", "description": "Directory to search in (relative to working dir or absolute)", "default": "."},
            },
        },
        execute=_find_files,
    )
)


ls_tool = register_tool(
    Tool(
        name="ls",
        description=(
            "List a directory's contents. Use this instead of `bash ls`. "
            "Returns file and directory names, sorted with directories first."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to list (relative to working dir or absolute)", "default": "."},
            },
        },
        execute=_list_dir,
    )
)
