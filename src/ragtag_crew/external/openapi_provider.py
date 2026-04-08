"""Fixed OpenAPI tool provider for a small set of preconfigured APIs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from ragtag_crew.config import settings
from ragtag_crew.external._utils import clip_text, truncate_output
from ragtag_crew.external.base import CapabilityStatus
from ragtag_crew.tools import Tool, register_tool


@dataclass(frozen=True)
class OpenAPIToolConfig:
    name: str
    description: str
    path: str
    method: str = "POST"
    presets: tuple[str, ...] = ("coding",)
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    request_body: dict[str, Any] | None = None
    query_params: dict[str, Any] | None = None
    result_mode: str = "json"
    timeout: int | None = None


@dataclass(frozen=True)
class OpenAPIProviderConfig:
    name: str
    base_url: str
    api_key: str = ""
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    tools: tuple[OpenAPIToolConfig, ...] = ()


def _config_path() -> Path:
    return Path(settings.openapi_tools_file).expanduser().resolve()


def _clean_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {key: _clean_value(item) for key, item in value.items()}
        return {key: item for key, item in cleaned.items() if item is not None}
    if isinstance(value, list):
        cleaned = [_clean_value(item) for item in value]
        return [item for item in cleaned if item is not None]
    return value


def _render_template(template: Any, arguments: dict[str, Any]) -> Any:
    if isinstance(template, str) and template.startswith("$"):
        return arguments.get(template[1:])
    if isinstance(template, dict):
        return _clean_value(
            {key: _render_template(value, arguments) for key, value in template.items()}
        )
    if isinstance(template, list):
        return _clean_value([_render_template(value, arguments) for value in template])
    return template


def load_openapi_provider_configs() -> list[OpenAPIProviderConfig]:
    path = _config_path()
    if not path.exists():
        return []

    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_providers = (
        payload.get("providers", []) if isinstance(payload, dict) else payload
    )
    providers: list[OpenAPIProviderConfig] = []
    for item in raw_providers:
        if not isinstance(item, dict):
            continue
        raw_tools = item.get("tools", [])
        tools: list[OpenAPIToolConfig] = []
        for raw_tool in raw_tools:
            if not isinstance(raw_tool, dict):
                continue
            tools.append(
                OpenAPIToolConfig(
                    name=str(raw_tool.get("name", "")).strip(),
                    description=str(raw_tool.get("description", "")).strip(),
                    path=str(raw_tool.get("path", "")).strip(),
                    method=str(raw_tool.get("method", "POST")).upper(),
                    presets=tuple(raw_tool.get("presets", ["coding"])),
                    parameters=raw_tool.get("parameters")
                    or {"type": "object", "properties": {}},
                    request_body=raw_tool.get("request_body"),
                    query_params=raw_tool.get("query_params"),
                    result_mode=str(raw_tool.get("result_mode", "json"))
                    .strip()
                    .lower(),
                    timeout=raw_tool.get("timeout"),
                )
            )
        providers.append(
            OpenAPIProviderConfig(
                name=str(item.get("name", "")).strip(),
                base_url=str(item.get("base_url", "")).rstrip("/"),
                api_key=str(item.get("api_key", "")).strip(),
                auth_header=str(item.get("auth_header", "Authorization")).strip(),
                auth_scheme=str(item.get("auth_scheme", "Bearer")).strip(),
                headers={
                    str(key): str(value)
                    for key, value in (item.get("headers") or {}).items()
                },
                enabled=bool(item.get("enabled", True)),
                tools=tuple(tools),
            )
        )
    return providers


def _build_url(
    provider: OpenAPIProviderConfig, tool: OpenAPIToolConfig, arguments: dict[str, Any]
) -> str:
    path = tool.path if tool.path.startswith("/") else f"/{tool.path}"
    for key in sorted(arguments.keys(), key=len, reverse=True):
        if arguments[key] is not None:
            path = path.replace(f"${key}", parse.quote(str(arguments[key]), safe=""))
    url = f"{provider.base_url}{path}"
    if tool.query_params:
        query_params = _render_template(tool.query_params, arguments)
        if isinstance(query_params, dict) and query_params:
            query_string = parse.urlencode(query_params, doseq=True)
            if query_string:
                url = f"{url}?{query_string}"
    return url


def _build_headers(provider: OpenAPIProviderConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json", **provider.headers}
    if provider.api_key:
        prefix = f"{provider.auth_scheme} " if provider.auth_scheme else ""
        headers[provider.auth_header] = f"{prefix}{provider.api_key}"
    return headers


def _format_json_result(payload: Any) -> str:
    return truncate_output(json.dumps(payload, ensure_ascii=False, indent=2))


def _format_search_result(payload: Any) -> str:
    if isinstance(payload, dict):
        raw_items = (
            payload.get("results")
            or payload.get("items")
            or payload.get("organic")
            or []
        )
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []

    items = [item for item in raw_items if isinstance(item, dict)]
    if not items:
        return "No OpenAPI search results returned."

    lines = ["OpenAPI search results:"]
    for index, item in enumerate(items, start=1):
        title = clip_text(item.get("title") or item.get("name") or item.get("url"))
        url = clip_text(item.get("url") or item.get("link"), limit=300)
        snippet = clip_text(
            item.get("snippet") or item.get("summary") or item.get("description"),
            limit=360,
        )
        lines.append(f"{index}. {title or url or '(untitled)'}")
        if url:
            lines.append(f"   URL: {url}")
        if snippet:
            lines.append(f"   Snippet: {snippet}")
    return truncate_output("\n".join(lines))


def _format_response(payload: Any, result_mode: str) -> str:
    if result_mode == "search_results":
        return _format_search_result(payload)
    return _format_json_result(payload)


def _fetch_json_response(req: request.Request, timeout: int) -> Any:
    with request.urlopen(req, timeout=timeout) as response:
        text = response.read().decode("utf-8")
    return json.loads(text) if text else {}


def _make_tool_executor(provider: OpenAPIProviderConfig, tool: OpenAPIToolConfig):
    async def _execute(**arguments: Any) -> str:
        url = _build_url(provider, tool, arguments)
        headers = _build_headers(provider)
        body = (
            _render_template(tool.request_body, arguments)
            if tool.request_body
            else None
        )
        data = (
            None
            if body is None or tool.method == "GET"
            else json.dumps(body).encode("utf-8")
        )
        req = request.Request(url, data=data, headers=headers, method=tool.method)
        timeout = tool.timeout or settings.openapi_timeout
        try:
            payload = await asyncio.to_thread(_fetch_json_response, req, timeout)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            return f"ERROR: OpenAPI tool HTTP {exc.code}: {detail or exc.reason}"
        except error.URLError as exc:
            return f"ERROR: OpenAPI tool request failed: {exc.reason}"
        except TimeoutError:
            return f"ERROR: OpenAPI tool timed out after {timeout}s."
        except Exception as exc:
            return f"ERROR: OpenAPI tool failed: {type(exc).__name__}: {exc}"
        return _format_response(payload, tool.result_mode)

    return _execute


def register_openapi_tools() -> list[CapabilityStatus]:
    providers = load_openapi_provider_configs()
    if not providers:
        return [
            CapabilityStatus(
                key="openapi",
                kind="openapi",
                ready=False,
                detail="no providers configured",
            )
        ]

    statuses: list[CapabilityStatus] = []
    for provider in providers:
        status_key = f"openapi:{provider.name or 'unnamed'}"
        if not provider.enabled:
            statuses.append(
                CapabilityStatus(
                    key=status_key, kind="openapi", ready=False, detail="disabled"
                )
            )
            continue
        if not provider.base_url:
            statuses.append(
                CapabilityStatus(
                    key=status_key,
                    kind="openapi",
                    ready=False,
                    detail="missing base_url",
                )
            )
            continue
        registered_names: list[str] = []
        for tool in provider.tools:
            if not tool.name or not tool.path:
                continue
            registered = register_tool(
                Tool(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters,
                    execute=_make_tool_executor(provider, tool),
                    source_type="openapi",
                    source_name=provider.name,
                    enabled_in_presets=tool.presets,
                )
            )
            registered_names.append(registered.name)
        ready = bool(registered_names)
        statuses.append(
            CapabilityStatus(
                key=status_key,
                kind="openapi",
                ready=ready,
                detail=f"base_url={provider.base_url}"
                if ready
                else "no tools configured",
                tool_names=tuple(registered_names),
            )
        )
    return statuses
