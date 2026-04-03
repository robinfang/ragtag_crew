"""Shell tools: bash.

Runs commands via asyncio subprocess, restricted to WORKING_DIR.
"""

from __future__ import annotations

import asyncio

from ragtag_crew.config import settings
from ragtag_crew.tools import Tool, register_tool
from ragtag_crew.tools.path_utils import get_working_dir

# Max chars returned to LLM to avoid blowing up context.
_OUTPUT_LIMIT = 50_000


async def _run_bash(command: str, timeout: int | None = None) -> str:
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
        return f"[TIMEOUT] Command exceeded {timeout}s limit."

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
            "Execute a shell command and return stdout/stderr.  "
            "The command runs in the agent working directory."
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
