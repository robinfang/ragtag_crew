"""External capability initialization and status tracking."""

from __future__ import annotations

import asyncio

from ragtag_crew.external.base import CapabilityStatus
from ragtag_crew.external.everything import register_everything_tool
from ragtag_crew.external.mcp_client import discover_mcp_tools

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


async def initialize_external_capabilities(*, force: bool = False) -> list[CapabilityStatus]:
    global _initialized
    if _initialized and not force:
        return get_capability_statuses()

    statuses = [register_everything_tool()]
    statuses.extend(await discover_mcp_tools())
    _initialized = True
    return _store_statuses(statuses)


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

    loop.create_task(initialize_external_capabilities())
