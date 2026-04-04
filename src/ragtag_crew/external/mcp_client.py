"""Minimal MCP client integration over stdio."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ragtag_crew.config import settings
from ragtag_crew.external.base import CapabilityStatus
from ragtag_crew.tools import Tool, register_tool


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    enabled: bool = True
    tool_prefix: str = ""
    presets: tuple[str, ...] = ("coding",)


def _config_path() -> Path:
    path = Path(settings.mcp_servers_file).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _normalize_server_config(raw: dict[str, Any]) -> MCPServerConfig:
    return MCPServerConfig(
        name=str(raw["name"]),
        command=str(raw["command"]),
        args=tuple(str(arg) for arg in raw.get("args", [])),
        env={str(key): str(value) for key, value in raw.get("env", {}).items()},
        cwd=str(raw["cwd"]) if raw.get("cwd") else None,
        enabled=bool(raw.get("enabled", True)),
        tool_prefix=str(raw.get("tool_prefix", "")).strip(),
        presets=tuple(str(item) for item in raw.get("presets", ["coding"])) or ("coding",),
    )


def load_mcp_server_configs() -> list[MCPServerConfig]:
    path = _config_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        servers = data.get("servers", [])
    elif isinstance(data, list):
        servers = data
    else:
        raise ValueError("MCP server config must be a list or an object with a 'servers' field")
    return [_normalize_server_config(item) for item in servers]


def _sanitize_tool_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()


def _tool_name(server: MCPServerConfig, remote_tool_name: str) -> str:
    prefix = server.tool_prefix or server.name
    return f"mcp_{_sanitize_tool_name(prefix)}_{_sanitize_tool_name(remote_tool_name)}"


def _tool_schema(remote_tool: Any) -> dict[str, Any]:
    schema = getattr(remote_tool, "inputSchema", None) or getattr(remote_tool, "input_schema", None)
    return schema if isinstance(schema, dict) else {"type": "object", "properties": {}}


def _resolve_server_cwd(server: MCPServerConfig) -> str | None:
    if not server.cwd:
        return None
    path = Path(server.cwd).expanduser()
    path = path if path.is_absolute() else Path.cwd() / path
    return str(path)


async def _list_tools_for_server(server: MCPServerConfig) -> list[Any]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=server.command,
        args=list(server.args),
        env=server.env or None,
        cwd=_resolve_server_cwd(server),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return list(getattr(result, "tools", []))


def _format_mcp_content_item(item: Any) -> str:
    text = getattr(item, "text", None)
    if isinstance(text, str) and text:
        return text
    if hasattr(item, "model_dump"):
        payload = item.model_dump(mode="json")
    elif hasattr(item, "dict"):
        payload = item.dict()
    elif hasattr(item, "__dict__"):
        payload = item.__dict__
    else:
        return str(item)
    return json.dumps(payload, ensure_ascii=False)


async def _call_tool_on_server(server: MCPServerConfig, remote_tool_name: str, arguments: dict[str, Any]) -> str:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=server.command,
        args=list(server.args),
        env=server.env or None,
        cwd=_resolve_server_cwd(server),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                remote_tool_name,
                arguments=arguments,
                read_timeout_seconds=settings.external_tool_timeout,
            )

    content = getattr(result, "content", []) or []
    text = "\n\n".join(_format_mcp_content_item(item) for item in content).strip()
    if getattr(result, "isError", False):
        return f"ERROR: {text or 'MCP tool returned an error.'}"
    return text or "(no output)"


def _build_registered_tool(server: MCPServerConfig, remote_tool: Any) -> Tool:
    remote_name = str(getattr(remote_tool, "name", "tool"))
    description = str(getattr(remote_tool, "description", "")).strip() or f"MCP tool '{remote_name}' from {server.name}."

    async def _execute(**kwargs: Any) -> str:
        return await _call_tool_on_server(server, remote_name, kwargs)

    return Tool(
        name=_tool_name(server, remote_name),
        description=description,
        parameters=_tool_schema(remote_tool),
        execute=_execute,
        source_type="mcp",
        source_name=server.name,
        enabled_in_presets=server.presets,
    )


async def discover_mcp_tools() -> list[CapabilityStatus]:
    statuses: list[CapabilityStatus] = []
    for server in load_mcp_server_configs():
        if not server.enabled:
            statuses.append(
                CapabilityStatus(
                    key=f"mcp:{server.name}",
                    kind="mcp",
                    ready=False,
                    detail="disabled",
                )
            )
            continue

        try:
            remote_tools = await _list_tools_for_server(server)
        except Exception as exc:
            statuses.append(
                CapabilityStatus(
                    key=f"mcp:{server.name}",
                    kind="mcp",
                    ready=False,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        registered_names: list[str] = []
        for remote_tool in remote_tools:
            tool = register_tool(_build_registered_tool(server, remote_tool))
            registered_names.append(tool.name)

        statuses.append(
            CapabilityStatus(
                key=f"mcp:{server.name}",
                kind="mcp",
                ready=True,
                detail=f"command={server.command}",
                tool_names=tuple(registered_names),
            )
        )
    return statuses
