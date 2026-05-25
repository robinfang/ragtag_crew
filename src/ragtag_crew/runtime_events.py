"""Runtime events emitted by ``AgentSession`` subscribers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from ragtag_crew.llm import ToolCall


@dataclass(frozen=True)
class AgentStartEvent:
    prompt_phase: str
    awaiting_plan_confirmation_at_start: bool


@dataclass(frozen=True)
class AgentEndEvent:
    content: str


@dataclass(frozen=True)
class TurnStartEvent:
    turn: int
    tools_enabled: bool


@dataclass(frozen=True)
class TurnEndEvent:
    turn: int


@dataclass(frozen=True)
class MessageStartEvent:
    pass


@dataclass(frozen=True)
class MessageUpdateEvent:
    delta: str


@dataclass(frozen=True)
class MessageEndEvent:
    content: str


@dataclass(frozen=True)
class ToolExecutionStartEvent:
    tool_call: ToolCall


@dataclass(frozen=True)
class ToolExecutionEndEvent:
    tool_call: ToolCall
    result: str


@dataclass(frozen=True)
class CancelledEvent:
    pass


@dataclass(frozen=True)
class ErrorEvent:
    error: BaseException | Exception


RuntimeEvent: TypeAlias = (
    AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionEndEvent
    | CancelledEvent
    | ErrorEvent
)
