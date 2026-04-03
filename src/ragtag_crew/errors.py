"""Shared domain exceptions for agent execution."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ragtag_crew.llm import LLMResponse


class RagtagCrewError(Exception):
    """Base exception for user-visible runtime errors."""


class LLMTimeoutError(RagtagCrewError, TimeoutError):
    """Raised when an LLM request exceeds the configured total timeout."""

    def __init__(self, timeout: int, partial_response: "LLMResponse | None" = None):
        super().__init__(f"LLM request exceeded {timeout}s limit.")
        self.timeout = timeout
        self.partial_response = partial_response


class LLMChunkTimeoutError(RagtagCrewError, TimeoutError):
    """Raised when streamed tokens stop arriving for too long."""

    def __init__(self, timeout: int, partial_response: "LLMResponse | None" = None):
        super().__init__(f"LLM stream stalled for {timeout}s without new output.")
        self.timeout = timeout
        self.partial_response = partial_response


class TurnTimeoutError(RagtagCrewError, TimeoutError):
    """Raised when a whole agent turn takes too long."""

    def __init__(self, timeout: int):
        super().__init__(f"Agent turn exceeded {timeout}s limit.")
        self.timeout = timeout
