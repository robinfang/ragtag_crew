"""Shared in-memory session cache across chat frontends."""

from __future__ import annotations

from ragtag_crew.agent import AgentSession
from ragtag_crew.session_store import SessionKey

SESSION_CACHE: dict[SessionKey, AgentSession] = {}


def drop_cached_session(session_key: SessionKey) -> None:
    SESSION_CACHE.pop(session_key, None)
