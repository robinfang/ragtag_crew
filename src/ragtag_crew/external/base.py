"""Shared types for external capability providers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityStatus:
    """Runtime status for one external capability source."""

    key: str
    kind: str
    ready: bool
    detail: str = ""
    tool_names: tuple[str, ...] = ()
