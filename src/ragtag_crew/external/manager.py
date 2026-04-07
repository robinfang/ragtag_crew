"""External capability initialization and status tracking."""

from __future__ import annotations

import asyncio
import logging

from ragtag_crew.external.base import CapabilityStatus
from ragtag_crew.external.browser_agent import register_browser_tools
from ragtag_crew.external.everything import register_everything_tool
from ragtag_crew.external.mcp_client import discover_mcp_tools
from ragtag_crew.external.openapi_provider import register_openapi_tools
from ragtag_crew.external.web_search import register_web_search_tool

log = logging.getLogger(__name__)

_capability_statuses: dict[str, CapabilityStatus] = {}
_initialized = False


def _store_statuses(statuses: list[CapabilityStatus]) -> list[CapabilityStatus]:
    global _capability_statuses
    _capability_statuses = {status.key: status for status in statuses}
    return get_capability_statuses()


def get_capability_statuses() -> list[CapabilityStatus]:
    return list(_capability_statuses.values())


def get_mcp_statuses() -> list[CapabilityStatus]:
    return [status for status in get_capability_statuses() if status.kind == "mcp"]


def get_browser_statuses() -> list[CapabilityStatus]:
    return [status for status in get_capability_statuses() if status.kind == "browser"]


def get_openapi_statuses() -> list[CapabilityStatus]:
    return [status for status in get_capability_statuses() if status.kind == "openapi"]


async def initialize_external_capabilities(
    *, force: bool = False
) -> list[CapabilityStatus]:
    global _initialized
    if _initialized and not force:
        return get_capability_statuses()

    statuses: list[CapabilityStatus] = []

    for label, registrar in [
        ("web_search", register_web_search_tool),
        ("everything", register_everything_tool),
    ]:
        try:
            statuses.append(registrar())
        except Exception:
            log.exception("Failed to register %s capability", label)

    try:
        statuses.extend(register_browser_tools())
    except Exception:
        log.exception("Failed to register browser capabilities")

    try:
        statuses.extend(register_openapi_tools())
    except Exception:
        log.exception("Failed to register OpenAPI capabilities")

    try:
        statuses.extend(await discover_mcp_tools())
    except Exception:
        log.exception("Failed to discover MCP tools")

    _initialized = bool(statuses)
    if not _initialized:
        log.warning(
            "External capability initialization produced no statuses; will retry later"
        )
    return _store_statuses(statuses)


def _on_deferred_init_done(task: asyncio.Task[list[CapabilityStatus]]) -> None:
    exc = task.exception()
    if exc is not None:
        log.error("Deferred external capability initialization failed: %s", exc)


def ensure_external_capabilities_initialized() -> None:
    """Initialize external capabilities in sync startup paths.

    `build_app()` is synchronous, but some tests call it inside an already
    running event loop. In that case, defer initialization to the loop instead
    of calling `asyncio.run(...)`.
    """

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(initialize_external_capabilities())
        return

    task = loop.create_task(initialize_external_capabilities())
    task.add_done_callback(_on_deferred_init_done)
