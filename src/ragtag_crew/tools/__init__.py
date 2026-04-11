"""Built-in tools for the agent.

Provides the Tool dataclass, tool presets, and a registry for looking up
tools by name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class Tool:
    """A single tool that the agent can invoke."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    execute: Callable[..., Awaitable[str]]
    source_type: str = "builtin"
    source_name: str = "builtin"
    enabled_in_presets: tuple[str, ...] = field(default_factory=tuple)

    def to_openai_schema(self) -> dict[str, Any]:
        """Return the tool definition in OpenAI function-calling format
        (used by litellm for all providers)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ALL_TOOLS: dict[str, Tool] = {}
_BUILTIN_TOOLS_REGISTERED = False


def register_tool(tool: Tool) -> Tool:
    """Register a tool so it can be looked up by name."""
    _ALL_TOOLS[tool.name] = tool
    return tool


def ensure_builtin_tools_registered() -> None:
    global _BUILTIN_TOOLS_REGISTERED
    if _BUILTIN_TOOLS_REGISTERED:
        return

    import ragtag_crew.tools.file_tools  # noqa: F401
    import ragtag_crew.tools.search_tools  # noqa: F401
    import ragtag_crew.tools.shell_tools  # noqa: F401
    import ragtag_crew.tools.workspace_tools  # noqa: F401

    _BUILTIN_TOOLS_REGISTERED = True


def get_tool(name: str) -> Tool:
    """Look up a registered tool by name.  Raises KeyError if unknown."""
    ensure_builtin_tools_registered()
    return _ALL_TOOLS[name]


def get_all_tools() -> dict[str, Tool]:
    """Return the full registry (name -> Tool)."""
    ensure_builtin_tools_registered()
    return dict(_ALL_TOOLS)


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

TOOL_PRESETS: dict[str, list[str]] = {
    "coding": ["read", "write", "edit", "bash", "grep", "find", "ls"],
    "readonly": ["read", "grep", "find", "ls"],
}


def get_tools_for_preset(preset: str) -> list[Tool]:
    """Return Tool instances for a named preset.

    Raises KeyError for an unknown preset. Only includes tools that are
    actually registered — silently skips any tool name that hasn't been
    registered yet.
    """
    ensure_builtin_tools_registered()
    names = TOOL_PRESETS.get(preset)
    if names is None:
        raise KeyError(f"Unknown tool preset: {preset}")

    tools: list[Tool] = []
    seen: set[str] = set()

    for name in names:
        tool = _ALL_TOOLS.get(name)
        if tool is None or tool.name in seen:
            continue
        tools.append(tool)
        seen.add(tool.name)

    for tool in _ALL_TOOLS.values():
        if preset not in tool.enabled_in_presets or tool.name in seen:
            continue
        tools.append(tool)
        seen.add(tool.name)

    return tools


def build_tool_schemas(tools: list[Tool]) -> list[dict[str, Any]]:
    """Build the ``tools`` parameter expected by ``litellm.acompletion``."""
    return [t.to_openai_schema() for t in tools]
