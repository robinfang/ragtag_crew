"""Shell tools: bash.

Runs commands via asyncio subprocess, restricted to WORKING_DIR.
"""

from __future__ import annotations

import asyncio
import re

from ragtag_crew.config import settings
from ragtag_crew.tools import Tool, register_tool
from ragtag_crew.tools.path_utils import get_working_dir

# Max chars returned to LLM to avoid blowing up context.
_OUTPUT_LIMIT = 50_000

_DELETE_PATTERN = re.compile(
    r"(?<![a-zA-Z0-9_])"
    r"(rm\b|del\b|rmdir\b|Remove-Item\b)",
    re.IGNORECASE,
)


def _check_delete_attempt(command: str) -> str | None:
    """Return an error message if the command looks like a file-deletion attempt."""
    if _DELETE_PATTERN.search(command):
        return (
            "ERROR: Direct file deletion via bash is not allowed. "
            "Use the `delete_file` tool to delete files or directories "
            "inside the working directory."
        )
    return None


async def _run_bash(command: str, timeout: int | None = None) -> str:
    if err := _check_delete_attempt(command):
        return err

    timeout = timeout or settings.bash_timeout
    cwd = str(get_working_dir())

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()  # reap
        return f"ERROR: Tool timeout after {timeout}s."
    except asyncio.CancelledError:
        proc.kill()
        await proc.communicate()
        raise

    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    parts: list[str] = []
    if out:
        parts.append(out)
    if err:
        parts.append(f"STDERR:\n{err}")
    if proc.returncode and proc.returncode != 0:
        parts.append(f"(exit code {proc.returncode})")
    result = "\n".join(parts) if parts else "(no output)"
    return result[:_OUTPUT_LIMIT]


bash_tool = register_tool(
    Tool(
        name="bash",
        description=(
            "Execute a shell command. Runs in the agent working directory. "
            "ONLY use for operations that built-in tools cannot handle: "
            "installing packages, running scripts, git operations, system commands, etc. "
            "Do NOT use for file search (use `grep`/`find`/`ls` instead), "
            "file reading/writing (use `read`/`write`/`edit` instead), "
            "or file deletion (use `delete_file` instead)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default from config)",
                },
            },
            "required": ["command"],
        },
        execute=_run_bash,
    )
)
