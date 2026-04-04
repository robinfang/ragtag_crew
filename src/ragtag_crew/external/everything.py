"""Windows Everything CLI integration."""

from __future__ import annotations

import asyncio
import platform
from pathlib import Path

from ragtag_crew.config import settings
from ragtag_crew.external.base import CapabilityStatus
from ragtag_crew.tools import Tool, register_tool
from ragtag_crew.tools.path_utils import resolve_path

_OUTPUT_LIMIT = 50_000


def _truncate(text: str) -> str:
    return text if len(text) <= _OUTPUT_LIMIT else text[:_OUTPUT_LIMIT] + "\n...[truncated]"


def _build_everything_command(query: str, path: str, max_results: int) -> list[str]:
    search_root = resolve_path(path)
    return [
        settings.everything_command,
        "-path",
        str(search_root),
        "-sort",
        "path",
        "-n",
        str(max_results),
        "-full-path-and-name",
        query,
    ]


async def _everything_search(query: str, path: str = ".", max_results: int | None = None) -> str:
    if not settings.everything_enabled:
        return "ERROR: Everything integration is disabled."
    if platform.system() != "Windows":
        return "ERROR: Everything search is only available on Windows."

    query = query.strip()
    if not query:
        return "ERROR: query must not be empty."

    limit = max_results or settings.everything_max_results
    command = _build_everything_command(query=query, path=path, max_results=limit)

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return f"ERROR: Everything CLI not found: {settings.everything_command}"

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=settings.everything_timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return f"ERROR: Everything search timed out after {settings.everything_timeout}s."
    except asyncio.CancelledError:
        proc.kill()
        await proc.communicate()
        raise

    output = stdout.decode(errors="replace").strip()
    error = stderr.decode(errors="replace").strip()
    if proc.returncode not in (0, 1):
        detail = error or f"es.exe exited with code {proc.returncode}"
        return f"ERROR: {detail}"
    if not output:
        return "No matches found."
    return _truncate(output)


def register_everything_tool() -> CapabilityStatus:
    """Register the Everything-backed search tool when enabled."""
    if not settings.everything_enabled:
        return CapabilityStatus(
            key="everything",
            kind="platform",
            ready=False,
            detail="disabled",
        )

    if platform.system() != "Windows":
        return CapabilityStatus(
            key="everything",
            kind="platform",
            ready=False,
            detail="windows-only",
        )

    tool = register_tool(
        Tool(
            name="everything_search",
            description=(
                "Search indexed files inside the current working tree on Windows "
                "via Everything CLI (es.exe)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Everything search query",
                    },
                    "path": {
                        "type": "string",
                        "description": "Search root inside WORKING_DIR",
                        "default": ".",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                    },
                },
                "required": ["query"],
            },
            execute=_everything_search,
            source_type="platform",
            source_name="everything",
            enabled_in_presets=("coding", "readonly"),
        )
    )
    return CapabilityStatus(
        key="everything",
        kind="platform",
        ready=True,
        detail=f"command={settings.everything_command}",
        tool_names=(tool.name,),
    )
