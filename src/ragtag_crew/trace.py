"""TraceCollector — 记录 agent 执行轨迹到 JSONL 文件。

每个 prompt() 调用产生一条完整的 JSON 记录，包含 turn 级别的
LLM 调用耗时、工具调用序列、结果状态等信息。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ragtag_crew.config import settings
from ragtag_crew.runtime_events import (
    AgentStartEvent,
    ErrorEvent,
    MessageEndEvent,
    RuntimeEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
    TurnStartEvent,
)

log = logging.getLogger(__name__)

_MAX_SNIPPET = 500


def _clip(text: str, limit: int = _MAX_SNIPPET) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _summarize_args(args: dict[str, Any], limit: int = _MAX_SNIPPET) -> str:
    if not args:
        return ""
    raw = json.dumps(args, ensure_ascii=False)
    return _clip(raw, limit)


@dataclass
class TraceRecordBuilder:
    """Consumes runtime events and builds one trace record."""

    trace_id: str
    session_key: str | None = None
    model: str = ""
    user_input: str = ""
    tool_preset: str = ""
    enabled_skills: list[str] = field(default_factory=list)
    planning_enabled: bool | None = None
    awaiting_plan_confirmation_at_start: bool | None = None
    prompt_phase: str = ""
    status: str = "success"
    error_info: str | None = None
    turns: list[dict[str, Any]] = field(default_factory=list)
    _start_time: float = field(default_factory=time.monotonic)
    _turn_start: float | None = None
    _tool_start: float | None = None
    _current_tool_name: str | None = None
    _current_turn: dict[str, Any] | None = None
    _current_tools: list[dict[str, Any]] = field(default_factory=list)

    def set_context(
        self,
        *,
        model: str,
        user_input: str,
        tool_preset: str = "",
        enabled_skills: list[str] | None = None,
        planning_enabled: bool | None = None,
    ) -> None:
        self.model = model
        self.user_input = _clip(user_input, 200)
        self.tool_preset = tool_preset
        self.enabled_skills = list(enabled_skills or [])
        self.planning_enabled = planning_enabled

    async def on_event(self, event: RuntimeEvent) -> None:
        match event:
            case AgentStartEvent(
                prompt_phase=prompt_phase,
                awaiting_plan_confirmation_at_start=awaiting,
            ):
                self._start_time = time.monotonic()
                self.prompt_phase = prompt_phase
                self.awaiting_plan_confirmation_at_start = awaiting

            case TurnStartEvent(turn=turn, tools_enabled=tools_enabled):
                self._turn_start = time.monotonic()
                self._current_turn = {"turn": turn, "tools_enabled": tools_enabled}
                self._current_tools = []

            case MessageEndEvent(content=content):
                if self._current_turn is not None and self._turn_start is not None:
                    self._current_turn["llm_time_ms"] = round(
                        (time.monotonic() - self._turn_start) * 1000
                    )
                    self._current_turn["response_len"] = len(content)
                    self._current_turn["has_content"] = bool(content)

            case ToolExecutionStartEvent(tool_call=tc):
                self._tool_start = time.monotonic()
                self._current_tool_name = tc.name

            case ToolExecutionEndEvent(tool_call=tc, result=result):
                tool_info: dict[str, Any] = {
                    "name": tc.name if tc else self._current_tool_name or "unknown",
                    "args_summary": _summarize_args(tc.arguments) if tc else "",
                    "result_len": len(result),
                    "status": "error" if result.startswith("ERROR:") else "success",
                }
                if self._tool_start is not None:
                    tool_info["duration_ms"] = round(
                        (time.monotonic() - self._tool_start) * 1000
                    )
                try:
                    from ragtag_crew.tools import get_tool as _get_tool

                    meta = _get_tool(tool_info["name"])
                    tool_info["source_type"] = meta.source_type
                    tool_info["source_name"] = meta.source_name
                except Exception:
                    pass
                self._current_tools.append(tool_info)
                self._tool_start = None
                self._current_tool_name = None

            case TurnEndEvent():
                if self._current_turn is not None:
                    self._current_turn["tool_calls"] = [
                        t["name"] for t in self._current_tools
                    ]
                    self._current_turn["tools"] = self._current_tools
                    self.turns.append(self._current_turn)
                self._current_turn = None
                self._current_tools = []
                self._turn_start = None

            case ErrorEvent(error=err):
                self.status = "error"
                self.error_info = f"{type(err).__name__}: {err}" if err else "unknown"

    def build_record(self) -> dict[str, Any]:
        total_ms = round((time.monotonic() - self._start_time) * 1000)
        return {
            "trace_id": self.trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_key": self.session_key,
            "model": self.model,
            "tool_preset": self.tool_preset,
            "enabled_skills": self.enabled_skills,
            "planning_enabled": self.planning_enabled,
            "awaiting_plan_confirmation_at_start": self.awaiting_plan_confirmation_at_start,
            "prompt_phase": self.prompt_phase,
            "user_input": self.user_input,
            "total_turns": len(self.turns),
            "total_time_ms": total_ms,
            "status": self.status,
            "error_info": self.error_info,
            "turns": self.turns,
        }


@dataclass(frozen=True)
class JsonlTraceSink:
    trace_dir: str

    def write(self, record: dict[str, Any]) -> Path | None:
        trace_dir = Path(self.trace_dir).resolve()
        trace_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filepath = trace_dir / f"{date_str}.jsonl"

        try:
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            return filepath
        except Exception:
            log.exception("Failed to write trace %s", record.get("trace_id", "unknown"))
            return None


class TraceCollector:
    """订阅 AgentSession 事件，累积执行轨迹，finalize() 时写出 JSONL。

    Usage::

        collector = TraceCollector(session_key=12345)
        session.subscribe(collector.on_event)
        await session.prompt(text)
        collector.finalize()
        session.unsubscribe(collector.on_event)
    """

    def __init__(self, session_key: int | str | None = None) -> None:
        self.session_key = None if session_key is None else str(session_key)
        self.trace_id = uuid.uuid4().hex[:12]
        self._builder = TraceRecordBuilder(
            trace_id=self.trace_id,
            session_key=self.session_key,
        )
        self._sink = JsonlTraceSink(trace_dir=settings.trace_dir)
        self.compaction_triggered = False

    def set_context(
        self,
        *,
        model: str,
        user_input: str,
        tool_preset: str = "",
        enabled_skills: list[str] | None = None,
        planning_enabled: bool | None = None,
    ) -> None:
        self._builder.set_context(
            model=model,
            user_input=user_input,
            tool_preset=tool_preset,
            enabled_skills=enabled_skills,
            planning_enabled=planning_enabled,
        )

    async def on_event(self, event: RuntimeEvent) -> None:
        if not settings.trace_enabled:
            return
        await self._builder.on_event(event)

    def finalize(self) -> Path | None:
        if not settings.trace_enabled:
            return None

        record = self._builder.build_record()
        filepath = self._sink.write(record)
        if filepath is not None:
            log.debug(
                "trace %s written to %s (%d turns, %dms)",
                self.trace_id,
                filepath,
                len(record["turns"]),
                record["total_time_ms"],
            )
        return filepath
