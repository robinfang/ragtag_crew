"""TraceCollector — 记录 agent 执行轨迹到 JSONL 文件。

每个 prompt() 调用产生一条完整的 JSON 记录，包含 turn 级别的
LLM 调用耗时、工具调用序列、结果状态等信息。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ragtag_crew.config import settings

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
        self._start_time = time.monotonic()
        self._turn_start: float | None = None
        self._tool_start: float | None = None
        self._current_tool_name: str | None = None
        self.status = "success"
        self.error_info: str | None = None

        self.turns: list[dict[str, Any]] = []
        self._current_turn: dict[str, Any] | None = None
        self._current_tools: list[dict[str, Any]] = []

        self.model: str = ""
        self.user_input: str = ""
        self.tool_preset: str = ""
        self.enabled_skills: list[str] = []
        self.planning_enabled: bool | None = None
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
        self.model = model
        self.user_input = _clip(user_input, 200)
        self.tool_preset = tool_preset
        self.enabled_skills = list(enabled_skills or [])
        self.planning_enabled = planning_enabled

    async def on_event(self, event_type: str, **kwargs: Any) -> None:
        if not settings.trace_enabled:
            return

        match event_type:
            case "agent_start":
                self._start_time = time.monotonic()

            case "turn_start":
                self._turn_start = time.monotonic()
                self._current_turn = {"turn": kwargs.get("turn", 0)}
                self._current_tools = []

            case "message_end":
                if self._current_turn is not None and self._turn_start is not None:
                    self._current_turn["llm_time_ms"] = round(
                        (time.monotonic() - self._turn_start) * 1000
                    )
                    content = kwargs.get("content", "") or ""
                    self._current_turn["response_len"] = len(content)
                    self._current_turn["has_content"] = bool(content)

            case "tool_execution_start":
                tc = kwargs.get("tool_call")
                if tc:
                    self._tool_start = time.monotonic()
                    self._current_tool_name = tc.name

            case "tool_execution_end":
                tc = kwargs.get("tool_call")
                result = kwargs.get("result", "") or ""
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

            case "turn_end":
                if self._current_turn is not None:
                    self._current_turn["tool_calls"] = [
                        t["name"] for t in self._current_tools
                    ]
                    self._current_turn["tools"] = self._current_tools
                    self.turns.append(self._current_turn)
                self._current_turn = None
                self._current_tools = []
                self._turn_start = None

            case "error":
                err = kwargs.get("error")
                self.status = "error"
                self.error_info = f"{type(err).__name__}: {err}" if err else "unknown"

    def _build_record(self) -> dict[str, Any]:
        total_ms = round((time.monotonic() - self._start_time) * 1000)
        return {
            "trace_id": self.trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_key": self.session_key,
            "model": self.model,
            "tool_preset": self.tool_preset,
            "enabled_skills": self.enabled_skills,
            "planning_enabled": self.planning_enabled,
            "user_input": self.user_input,
            "total_turns": len(self.turns),
            "total_time_ms": total_ms,
            "status": self.status,
            "error_info": self.error_info,
            "turns": self.turns,
        }

    def finalize(self) -> Path | None:
        if not settings.trace_enabled:
            return None

        record = self._build_record()
        trace_dir = Path(settings.trace_dir).resolve()
        trace_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filepath = trace_dir / f"{date_str}.jsonl"

        try:
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            log.debug(
                "trace %s written to %s (%d turns, %dms)",
                self.trace_id,
                filepath,
                len(self.turns),
                record["total_time_ms"],
            )
            return filepath
        except Exception:
            log.exception("Failed to write trace %s", self.trace_id)
            return None
