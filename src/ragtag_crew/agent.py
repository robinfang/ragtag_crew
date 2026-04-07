"""AgentSession — self-built agent loop.

Drives the  LLM <-> tool-call  cycle and emits events that the
Telegram streaming layer can subscribe to.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Awaitable

from ragtag_crew.config import settings
from ragtag_crew.context_builder import build_system_prompt
from ragtag_crew.errors import (
    LLMChunkTimeoutError,
    LLMTimeoutError,
    TurnTimeoutError,
    UserAbortedError,
)
from ragtag_crew.external.browser_agent import browser_execution_context
from ragtag_crew.llm import stream_chat, LLMResponse, ToolCall
from ragtag_crew.session_summary import clear_stale_tool_results, compact_history
from ragtag_crew.tools import Tool, build_tool_schemas, get_tool

log = logging.getLogger(__name__)

# Type alias for event callbacks.
EventCallback = Callable[..., Awaitable[None]]


class AgentSession:
    """A single agent conversation with tool-calling support.

    Events emitted (via subscribed callbacks):
        agent_start()
        turn_start(turn=int)
        message_start()
        message_update(delta=str)
        message_end(content=str)
        tool_execution_start(tool_call=ToolCall)
        tool_execution_end(tool_call=ToolCall, result=str)
        turn_end(turn=int)
        cancelled()
        agent_end(content=str)
        error(error=Exception)
    """

    def __init__(
        self,
        model: str,
        tools: list[Tool],
        system_prompt: str = "",
        tool_preset: str = "coding",
        enabled_skills: list[str] | None = None,
        session_prompt: str = "",
        session_summary: str = "",
        summary_updated_at: float | None = None,
        recent_message_count: int = 0,
        browser_mode: str = "isolated",
        browser_attached_confirmed: bool = False,
        planning_enabled: bool | None = None,
    ):
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.tool_preset = tool_preset
        self.enabled_skills = list(enabled_skills or [])
        self.session_prompt = session_prompt
        self.session_summary = session_summary
        self.summary_updated_at = summary_updated_at
        self.recent_message_count = recent_message_count
        self.browser_mode = browser_mode
        self.browser_attached_confirmed = browser_attached_confirmed
        self.planning_enabled = (
            planning_enabled
            if planning_enabled is not None
            else settings.planning_enabled
        )
        self.messages: list[dict[str, Any]] = []

        self._callbacks: list[EventCallback] = []
        self._abort_event = asyncio.Event()
        self._busy = False
        self._active_started_at: float | None = None
        self._active_turn = 0
        self._completed_turns = 0
        self._completed_tools = 0
        self._active_tool_name: str | None = None
        self._last_tool_name: str | None = None
        self._response_preview = ""
        self._active_request_text = ""

    # -- public state -------------------------------------------------------

    @property
    def is_busy(self) -> bool:
        return self._busy

    def render_progress_text(self) -> str:
        """Return a short runtime snapshot for busy-state progress queries."""
        if not self._busy:
            return "当前没有进行中的任务。"

        lines = ["任务仍在执行。"]
        if self._active_started_at is not None:
            lines.append(f"已运行: {self._format_elapsed(self._active_started_at)}")
        if self._active_request_text:
            lines.append(
                f"当前请求: {self._clip_progress_text(self._active_request_text, 80)}"
            )
        if self._active_turn:
            lines.append(f"当前轮次: {self._active_turn}")
        if self._completed_turns:
            lines.append(f"已完成轮次: {self._completed_turns}")
        if self._completed_tools:
            lines.append(f"已执行工具: {self._completed_tools} 次")
        if self._active_tool_name:
            lines.append(f"正在执行: {self._active_tool_name}")
        elif self._last_tool_name:
            lines.append(f"最近一步: {self._last_tool_name}")
        if self._response_preview:
            lines.append(f"最近输出: {self._response_preview}")
        lines.append("如需中止，发送 /cancel。")
        return "\n".join(lines)

    # -- subscription -------------------------------------------------------

    def subscribe(self, cb: EventCallback) -> None:
        self._callbacks.append(cb)

    def unsubscribe(self, cb: EventCallback) -> None:
        self._callbacks = [c for c in self._callbacks if c is not cb]

    async def _emit(self, event_type: str, **kwargs: Any) -> None:
        for cb in self._callbacks:
            try:
                await cb(event_type, **kwargs)
            except Exception:
                log.exception("Error in event callback for %s", event_type)

    # -- control ------------------------------------------------------------

    def abort(self) -> None:
        """Signal the current prompt() to stop as soon as possible."""
        self._abort_event.set()

    def reset(self) -> None:
        """Clear conversation history (start a new session)."""
        self.messages.clear()
        self.session_prompt = ""
        self.session_summary = ""
        self.summary_updated_at = None
        self.recent_message_count = 0
        self.browser_attached_confirmed = False

    def compact(self, *, force: bool = False) -> bool:
        """Compact older history into ``session_summary``.

        When ``force`` is false, compaction follows the configured trigger.
        When ``force`` is true, it compacts whenever history exceeds the
        configured recent-message window.
        """
        before_messages = list(self.messages)
        before_summary = self.session_summary
        self._maybe_compact_history(force=force)
        return (
            self.messages != before_messages or self.session_summary != before_summary
        )

    # -- core loop ----------------------------------------------------------

    async def prompt(self, text: str) -> str:
        """Send user text and run the agent loop until completion.

        Returns the final assistant text (also emitted via events).
        """
        if self._busy:
            raise RuntimeError("Session is already processing a prompt")

        self._busy = True
        self._abort_event.clear()
        self._active_started_at = time.monotonic()
        self._active_turn = 0
        self._completed_turns = 0
        self._completed_tools = 0
        self._active_tool_name = None
        self._last_tool_name = None
        self._response_preview = ""
        self._active_request_text = self._clip_progress_text(text, 120)
        self.messages.append({"role": "user", "content": text})
        msg_before_prompt = len(self.messages) - 1

        await self._emit("agent_start")
        final_content = ""

        try:
            final_content = await asyncio.wait_for(
                self._run_loop(),
                timeout=settings.turn_timeout,
            )
        except (LLMTimeoutError, LLMChunkTimeoutError):
            raise
        except UserAbortedError:
            await self._emit("cancelled")
            raise
        except asyncio.TimeoutError as exc:
            self._abort_event.set()
            timeout_error = TurnTimeoutError(settings.turn_timeout)
            await self._emit("error", error=timeout_error)
            raise timeout_error from exc
        except Exception as exc:
            log.exception("Agent loop error")
            await self._emit("error", error=exc)
            raise
        else:
            if (
                settings.verify_enabled
                and final_content
                and self._detect_file_modifications(msg_before_prompt)
            ):
                final_content = await self._run_verify_phase()

            return final_content
        finally:
            self._maybe_compact_history()
            self._busy = False
            self._active_started_at = None
            self._active_turn = 0
            self._active_tool_name = None
            self._active_request_text = ""
            await self._emit("agent_end", content=final_content)

    async def _on_text_delta(self, delta: str) -> None:
        if delta:
            preview = self._response_preview + delta
            self._response_preview = self._clip_progress_text(preview, 160)
        await self._emit("message_update", delta=delta)

    @staticmethod
    def _clip_progress_text(text: str, limit: int) -> str:
        clean = " ".join(text.split())
        if len(clean) <= limit:
            return clean
        return clean[: limit - 3].rstrip() + "..."

    @staticmethod
    def _format_elapsed(started_at: float) -> str:
        elapsed = max(0, round(time.monotonic() - started_at))
        minutes, seconds = divmod(elapsed, 60)
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def _append_assistant_message(self, response: LLMResponse) -> None:
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if response.content:
            assistant_msg["content"] = response.content
        if response.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in response.tool_calls
            ]
        self.messages.append(assistant_msg)

    def _maybe_compact_history(self, *, force: bool = False) -> None:
        self.recent_message_count = len(self.messages)
        trigger = settings.session_summary_trigger_messages
        keep_count = max(settings.session_summary_recent_messages, 1)

        if force:
            if len(self.messages) <= keep_count:
                return
        elif trigger <= 0 or len(self.messages) <= trigger:
            return

        summary, recent_messages = compact_history(
            messages=self.messages,
            previous_summary=self.session_summary,
            recent_message_count=keep_count,
            max_chars=settings.session_summary_max_chars,
        )
        if recent_messages == self.messages and summary == self.session_summary:
            return

        self.messages = recent_messages
        self.messages = clear_stale_tool_results(
            self.messages,
            keep_recent=settings.tool_result_keep_recent,
        )
        self.session_summary = summary
        self.summary_updated_at = time.time()
        self.recent_message_count = len(self.messages)

    def _build_messages(self) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        system_prompt = build_system_prompt(
            base_system_prompt=self.system_prompt,
            enabled_skills=self.enabled_skills,
            session_prompt=self.session_prompt,
            session_summary=self.session_summary,
            planning_enabled=self.planning_enabled,
        )
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.extend(self.messages)
        return msgs

    async def _execute_tool(self, tc: ToolCall) -> str:
        if self._abort_event.is_set():
            return "ABORTED"
        try:
            tool = get_tool(tc.name)
        except KeyError:
            return f"ERROR: unknown tool '{tc.name}'"
        try:
            with browser_execution_context(
                self.browser_mode,
                attached_confirmed=self.browser_attached_confirmed,
            ):
                result = await tool.execute(**tc.arguments)
        except Exception as exc:
            log.exception("Tool %s execution failed", tc.name)
            result = f"ERROR: {type(exc).__name__}: {exc}"
        if self._abort_event.is_set():
            return "ABORTED"
        return result

    _MODIFYING_TOOLS = frozenset({"write_file", "edit_file", "delete_file"})

    def _detect_file_modifications(self, since_index: int) -> bool:
        for msg in self.messages[since_index:]:
            if (
                msg.get("role") == "tool"
                and msg.get("tool_name") in self._MODIFYING_TOOLS
            ):
                return True
        return False

    async def _run_verify_phase(self) -> str:
        commands = settings.verify_commands.strip()
        if not commands:
            return self._response_preview or ""
        prompt_text = settings.verify_prompt.format(commands=commands)
        self.messages.append({"role": "user", "content": prompt_text})
        log.info("[verify] injected verification prompt (%d chars)", len(prompt_text))
        try:
            content = await asyncio.wait_for(
                self._run_loop(max_turns=settings.verify_max_turns),
                timeout=settings.turn_timeout,
            )
        except (LLMTimeoutError, LLMChunkTimeoutError):
            raise
        except UserAbortedError:
            await self._emit("cancelled")
            raise
        except asyncio.TimeoutError:
            self._abort_event.set()
            raise
        except Exception:
            log.exception("[verify] verification phase error, ignoring")
            content = self._response_preview or ""
        return content or self._response_preview or ""

    async def _run_loop(self, max_turns: int | None = None) -> str:
        tool_schemas = build_tool_schemas(self.tools) if self.tools else None
        last_content = ""

        for turn in range(1, (max_turns or settings.max_turns) + 1):
            if self._abort_event.is_set():
                raise UserAbortedError()

            self._active_turn = turn
            await self._emit("turn_start", turn=turn)
            await self._emit("message_start")

            try:
                response: LLMResponse = await stream_chat(
                    model=self.model,
                    messages=self._build_messages(),
                    tools=tool_schemas,
                    on_delta=self._on_text_delta,
                    should_abort=self._abort_event.is_set,
                )
            except (LLMTimeoutError, LLMChunkTimeoutError) as exc:
                response = exc.partial_response or LLMResponse()
                if response.content:
                    self._response_preview = self._clip_progress_text(
                        response.content, 160
                    )
                await self._emit("message_end", content=response.content)
                self._append_assistant_message(response)
                last_content = response.content
                raise

            if response.content:
                self._response_preview = self._clip_progress_text(response.content, 160)
            await self._emit("message_end", content=response.content)

            self._append_assistant_message(response)

            last_content = response.content

            if self._abort_event.is_set():
                await self._emit("turn_end", turn=turn)
                raise UserAbortedError()

            if not response.tool_calls:
                await self._emit("turn_end", turn=turn)
                break

            for tc in response.tool_calls:
                if self._abort_event.is_set():
                    raise UserAbortedError()

                self._active_tool_name = tc.name
                await self._emit("tool_execution_start", tool_call=tc)
                result = await self._execute_tool(tc)
                self._active_tool_name = None
                self._last_tool_name = tc.name
                self._completed_tools += 1
                await self._emit("tool_execution_end", tool_call=tc, result=result)

                tool_meta = get_tool(tc.name)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "tool_name": tc.name,
                        "tool_source_type": tool_meta.source_type,
                        "tool_source_name": tool_meta.source_name,
                        "content": result,
                    }
                )

            await self._emit("turn_end", turn=turn)
            self._completed_turns = turn
        else:
            log.warning("Agent loop hit max_turns (%d)", settings.max_turns)

        return last_content
