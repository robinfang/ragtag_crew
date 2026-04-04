"""External capability layer for platform and remote tools."""

from ragtag_crew.external.base import CapabilityStatus
from ragtag_crew.external.manager import (
    get_browser_statuses,
    ensure_external_capabilities_initialized,
    get_capability_statuses,
    get_mcp_statuses,
    initialize_external_capabilities,
)

__all__ = [
    "CapabilityStatus",
    "get_browser_statuses",
    "ensure_external_capabilities_initialized",
    "get_capability_statuses",
    "get_mcp_statuses",
    "initialize_external_capabilities",
]
