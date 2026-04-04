"""Minimal web search API integration."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from ragtag_crew.config import settings
from ragtag_crew.external.base import CapabilityStatus
from ragtag_crew.tools import Tool, register_tool

_OUTPUT_LIMIT = 50_000


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


def _truncate(text: str) -> str:
    return text if len(text) <= _OUTPUT_LIMIT else text[:_OUTPUT_LIMIT] + "\n...[truncated]"


def _clip(value: Any, limit: int = 240) -> str:
    text = value if isinstance(value, str) else ""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _build_search_request(query: str, num_results: int) -> request.Request:
    provider = settings.web_search_provider.lower()
    headers = {"Content-Type": "application/json"}
    if settings.web_search_api_key:
        if provider == "serper":
            headers["X-API-KEY"] = settings.web_search_api_key
        else:
            headers["Authorization"] = f"Bearer {settings.web_search_api_key}"

    if provider == "serper":
        payload = {"q": query, "num": num_results}
    else:
        payload = {"query": query, "num_results": num_results}

    return request.Request(
        settings.web_search_api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )


def _normalize_search_results(payload: dict[str, Any]) -> list[SearchResult]:
    provider = settings.web_search_provider.lower()
    items: list[dict[str, Any]]
    if provider == "serper":
        items = list(payload.get("organic", []))
    else:
        raw = payload.get("results", [])
        items = [item for item in raw if isinstance(item, dict)]

    results: list[SearchResult] = []
    for item in items:
        title = _clip(item.get("title") or item.get("name"))
        url = _clip(item.get("link") or item.get("url"), limit=300)
        snippet = _clip(item.get("snippet") or item.get("description"), limit=360)
        if not url:
            continue
        results.append(SearchResult(title=title or url, url=url, snippet=snippet))
    return results


def _format_results(query: str, results: list[SearchResult]) -> str:
    if not results:
        return f"No web search results found for: {query}"

    lines = [f"Web search results for: {query}"]
    for index, result in enumerate(results, start=1):
        lines.append(f"{index}. {result.title}")
        lines.append(f"   URL: {result.url}")
        if result.snippet:
            lines.append(f"   Snippet: {result.snippet}")
    return _truncate("\n".join(lines))


async def _web_search(query: str, num_results: int | None = None) -> str:
    if not settings.web_search_enabled:
        return "ERROR: Web search integration is disabled."
    query = query.strip()
    if not query:
        return "ERROR: query must not be empty."

    count = num_results or settings.web_search_max_results
    req = _build_search_request(query, count)

    try:
        payload = await asyncio.to_thread(_fetch_payload, req)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        return f"ERROR: web search HTTP {exc.code}: {detail or exc.reason}"
    except error.URLError as exc:
        return f"ERROR: web search request failed: {exc.reason}"
    except TimeoutError:
        return f"ERROR: web search timed out after {settings.web_search_timeout}s."
    except Exception as exc:
        return f"ERROR: web search failed: {type(exc).__name__}: {exc}"

    results = _normalize_search_results(payload)
    return _format_results(query, results)


def _fetch_payload(req: request.Request) -> dict[str, Any]:
    with request.urlopen(req, timeout=settings.web_search_timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def register_web_search_tool() -> CapabilityStatus:
    if not settings.web_search_enabled:
        return CapabilityStatus(
            key="web-search",
            kind="search",
            ready=False,
            detail="disabled",
        )

    tool = register_tool(
        Tool(
            name="web_search",
            description=(
                "Search the web with the configured search API and return titles, URLs, and snippets."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Maximum number of search results to return",
                    },
                },
                "required": ["query"],
            },
            execute=_web_search,
            source_type="search",
            source_name=settings.web_search_provider,
            enabled_in_presets=("coding", "readonly"),
        )
    )
    return CapabilityStatus(
        key="web-search",
        kind="search",
        ready=True,
        detail=f"provider={settings.web_search_provider}",
        tool_names=(tool.name,),
    )
