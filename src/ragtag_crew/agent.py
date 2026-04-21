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
from ragtag_crew.memory_store import append_memory_note_if_missing
from ragtag_crew.session_summary import (
    _clip_text,
    _extract_external_refs,
    build_compression_block,
    clear_stale_tool_results,
    compact_history,
    render_compression_blocks,
)
from ragtag_crew.tools import Tool, build_tool_schemas, get_tool

log = logging.getLogger(__name__)

# Type alias for event callbacks.
EventCallback = Callable[..., Awaitable[None]]

_PLAN_ACTION_KEYWORDS = (
    "implement",
    "add",
    "update",
    "change",
    "modify",
    "refactor",
    "fix",
    "debug",
    "investigate",
    "search",
    "check",
    "analyze",
    "plan",
    "run",
    "test",
    "build",
    "write",
    "create",
    "generate",
    "实现",
    "新增",
    "添加",
    "修改",
    "更新",
    "重构",
    "修复",
    "排查",
    "检查",
    "分析",
    "搜索",
    "查找",
    "生成",
    "创建",
    "编写",
    "整理",
    "迁移",
    "集成",
    "接入",
    "运行",
    "执行",
    "测试",
    "构建",
    "方案",
    "脚本",
    "trace",
    "看下",
    "看看",
)

_PLAN_CONFIRMATIONS = frozenset(
    {
        "继续",
        "继续吧",
        "开始",
        "执行",
        "开工",
        "确认",
        "可以",
        "按这个做",
        "yes",
        "go",
        "run",
        "start",
        "proceed",
        "continue",
    }
)

_PLANNING_FALLBACK = (
    "1. 先确认现状与相关代码路径。\n"
    "2. 再按最小正确改动实现需求。\n"
    "3. 最后补测试并验证结果。\n\n"
    "请回复“继续”开始执行，或直接发送修改意见。"
)

_PLANNING_INSTRUCTION = (
    "你当前处于 planning phase。"
    "只输出一个简洁的编号计划，不要调用工具，不要声称已经开始执行。"
    "最后一行明确提示用户回复“继续”后才会开始执行，"
    "或者直接发送新的修改意见来调整计划。"
)


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
        protected_content: str = "",
        compression_blocks: list[dict[str, Any]] | None = None,
        session_summary: str = "",
        summary_updated_at: float | None = None,
        recent_message_count: int = 0,
        browser_mode: str = "isolated",
        browser_attached_confirmed: bool = False,
        planning_enabled: bool | None = None,
        awaiting_plan_confirmation: bool = False,
        pending_plan_text: str = "",
        pending_plan_request_text: str = "",
        plan_generated_at: float | None = None,
    ):
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.tool_preset = tool_preset
        self.enabled_skills = list(enabled_skills or [])
        self.session_prompt = session_prompt
        self.protected_content = protected_content
        self.compression_blocks = list(compression_blocks or [])
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
        self.awaiting_plan_confirmation = awaiting_plan_confirmation
        self.pending_plan_text = pending_plan_text
        self.pending_plan_request_text = pending_plan_request_text
        self.plan_generated_at = plan_generated_at
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
        if self.awaiting_plan_confirmation:
            lines = ["当前正在等待你确认计划。"]
            if self.pending_plan_request_text:
                lines.append(
                    f"原始请求: {self._clip_progress_text(self.pending_plan_request_text, 80)}"
                )
            if self.pending_plan_text:
                lines.append(
                    f"计划摘要: {self._clip_progress_text(self.pending_plan_text, 100)}"
                )
            lines.append("回复“继续”即可开始执行，也可以直接发送新的修改意见。")
            return "\n".join(lines)

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
        self.protected_content = ""
        self.compression_blocks = []
        self.session_summary = ""
        self.summary_updated_at = None
        self.recent_message_count = 0
        self.browser_attached_confirmed = False
        self._clear_pending_plan_state()

    def clear_pending_plan(self) -> None:
        """Clear any waiting-for-confirmation planning state."""
        self._clear_pending_plan_state()

    async def compact(self, *, force: bool = False) -> bool:
        """Compact older history into ``session_summary``.

        When ``force`` is false, compaction follows the configured trigger.
        When ``force`` is true, it compacts whenever history exceeds the
        configured recent-message window.
        """
        before_messages = list(self.messages)
        before_summary = self.session_summary
        await self._maybe_compact_history(force=force)
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

        awaiting_plan_confirmation_at_start = self.awaiting_plan_confirmation
        if not self.planning_enabled and self.awaiting_plan_confirmation:
            self._clear_pending_plan_state()
            awaiting_plan_confirmation_at_start = False

        if self.awaiting_plan_confirmation and self._is_plan_confirmation(text):
            self._clear_pending_plan_state()
            prompt_phase = "execution"
        else:
            if self.awaiting_plan_confirmation:
                self._clear_pending_plan_state()
            prompt_phase = (
                "planning"
                if self._should_require_plan(text)
                else "execution"
            )

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

        await self._emit(
            "agent_start",
            prompt_phase=prompt_phase,
            awaiting_plan_confirmation_at_start=awaiting_plan_confirmation_at_start,
        )
        final_content = ""

        try:
            runner = (
                self._run_planning_phase()
                if prompt_phase == "planning"
                else self._run_loop()
            )
            final_content = await asyncio.wait_for(runner, timeout=settings.turn_timeout)
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
            await self._maybe_compact_history()
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

    async def _maybe_compact_history(self, *, force: bool = False) -> None:
        trigger = settings.session_summary_trigger_messages
        keep_count = max(settings.session_summary_recent_messages, 1)

        if force:
            if len(self.messages) <= keep_count:
                return
        elif trigger <= 0 or len(self.messages) <= trigger:
            return

        split_index = len(self.messages) - keep_count
        older_messages = self.messages[:split_index]
        await self._maybe_capture_precompact_memory(older_messages)

        summary, recent_messages = compact_history(
            messages=self.messages,
            previous_summary=self.session_summary,
            recent_message_count=keep_count,
            max_chars=settings.session_summary_max_chars,
        )
        if recent_messages == self.messages and summary == self.session_summary:
            return

        block = build_compression_block(older_messages)
        if block is not None:
            self.compression_blocks.append(block.to_dict())

        self.messages = recent_messages
        self.messages = clear_stale_tool_results(
            self.messages,
            keep_recent=settings.tool_result_keep_recent,
        )
        self.session_summary = summary
        self.summary_updated_at = time.time()
        self.recent_message_count = len(self.messages)

    def _build_messages(self) -> list[dict[str, Any]]:
        return self._build_messages_with_extra_prompt()

    def _build_messages_with_extra_prompt(
        self, extra_system_prompt: str | None = None
    ) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        system_prompt = build_system_prompt(
            base_system_prompt=self.system_prompt,
            enabled_skills=self.enabled_skills,
            protected_content=self.protected_content,
            compression_blocks=render_compression_blocks(self.compression_blocks),
            session_prompt=self.session_prompt,
            session_summary=self.session_summary,
            planning_enabled=self.planning_enabled,
        )
        if extra_system_prompt:
            system_prompt = (
                f"{system_prompt}\n\n{extra_system_prompt}"
                if system_prompt
                else extra_system_prompt
            )
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.extend(self.messages)
        return msgs

    def _clear_pending_plan_state(self) -> None:
        self.awaiting_plan_confirmation = False
        self.pending_plan_text = ""
        self.pending_plan_request_text = ""
        self.plan_generated_at = None

    def _is_plan_confirmation(self, text: str) -> bool:
        normalized = " ".join(text.lower().split())
        return normalized in _PLAN_CONFIRMATIONS

    def _should_require_plan(self, text: str) -> bool:
        if not self.planning_enabled:
            return False

        normalized = " ".join(text.lower().split())
        if not normalized:
            return False
        if "\n" in text:
            return True
        if len(normalized) >= 60:
            return True
        return any(keyword in normalized for keyword in _PLAN_ACTION_KEYWORDS)

    async def _run_planning_phase(self) -> str:
        turn = 1
        self._active_turn = turn
        await self._emit("turn_start", turn=turn, tools_enabled=False)
        await self._emit("message_start")

        try:
            response: LLMResponse = await stream_chat(
                model=self.model,
                messages=self._build_messages_with_extra_prompt(_PLANNING_INSTRUCTION),
                tools=None,
                on_delta=self._on_text_delta,
                should_abort=self._abort_event.is_set,
            )
        except (LLMTimeoutError, LLMChunkTimeoutError) as exc:
            response = exc.partial_response or LLMResponse()
            if response.content:
                self._response_preview = self._clip_progress_text(response.content, 160)
            await self._emit("message_end", content=response.content)
            self._append_assistant_message(response)
            await self._emit("turn_end", turn=turn)
            self._completed_turns = turn
            raise

        content = response.content.strip() or _PLANNING_FALLBACK
        response.content = content
        self._response_preview = self._clip_progress_text(content, 160)
        self.awaiting_plan_confirmation = True
        self.pending_plan_text = content
        latest_user_text = self.messages[-1].get("content", "") if self.messages else ""
        self.pending_plan_request_text = latest_user_text if isinstance(latest_user_text, str) else ""
        self.plan_generated_at = time.time()
        await self._emit("message_end", content=content)
        self._append_assistant_message(response)
        await self._emit("turn_end", turn=turn)
        self._completed_turns = turn
        return content

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

    _MODIFYING_TOOLS = frozenset(
        {
            "write",
            "write_file",
            "edit",
            "edit_file",
            "delete_file",
            "create_workspace",
            "delete_workspace",
            "cleanup_workspaces",
            "write_script",
        }
    )

    def _detect_file_modifications(self, since_index: int) -> bool:
        for msg in self.messages[since_index:]:
            if (
                msg.get("role") == "tool"
                and msg.get("tool_name") in self._MODIFYING_TOOLS
            ):
                return True
        return False

    def _extract_precompact_memory_notes(
        self, messages: list[dict[str, Any]]
    ) -> list[str]:
        if not settings.auto_memory_precompact_enabled:
            return []

        markers = {
            item.strip().lower()
            for item in settings.auto_memory_precompact_markers.split(",")
            if item.strip()
        }
        if not markers:
            return []

        notes: list[str] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content", "")
            if role not in {"user", "assistant"} or not isinstance(content, str):
                continue

            normalized = " ".join(content.split()).strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            if not any(marker in lowered for marker in markers):
                continue

            prefix = "user" if role == "user" else "assistant"
            excerpt = _clip_text(
                normalized,
                limit=settings.auto_memory_precompact_max_excerpt_chars,
            )
            notes.append(f"[precompact/{prefix}] {excerpt}")
        return notes

    async def _maybe_capture_precompact_memory(
        self, messages: list[dict[str, Any]]
    ) -> None:
        for note in self._extract_precompact_memory_notes(messages):
            try:
                path, appended = await asyncio.to_thread(
                    append_memory_note_if_missing, note
                )
                if appended:
                    log.info("[memory] captured precompact note to %s", path.name)
            except Exception:
                log.exception("[memory] failed to capture precompact note")

    async def _maybe_capture_external_memory(self, tool: Tool, result: str) -> None:
        if not settings.auto_memory_external_results_enabled:
            return
        if tool.source_type in {"local", "builtin"}:
            return
        allowed_source_types = {
            item.strip()
            for item in settings.auto_memory_external_source_types.split(",")
            if item.strip()
        }
        if allowed_source_types and tool.source_type not in allowed_source_types:
            return
        if not isinstance(result, str) or not result or result.startswith("ERROR:"):
            return

        refs = _extract_external_refs(result)
        if not refs:
            return

        excerpt = _clip_text(
            result,
            limit=settings.auto_memory_external_max_excerpt_chars,
        )
        source_name = tool.source_name or tool.name
        note = f"[{tool.source_type}/{source_name}] {excerpt} | Refs: {refs}"
        try:
            path, appended = await asyncio.to_thread(
                append_memory_note_if_missing, note
            )
            if appended:
                log.info("[memory] captured external result to %s", path.name)
        except Exception:
            log.exception("[memory] failed to capture external result")

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
            await self._emit("turn_start", turn=turn, tools_enabled=bool(tool_schemas))
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
                await self._maybe_capture_external_memory(tool_meta, result)
                content = result
                if tool_meta.source_type not in {"local", "builtin"}:
                    source_name = tool_meta.source_name or tool_meta.name
                    content = f"[来源: {tool_meta.source_type}/{source_name}]\n{result}"
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "tool_name": tc.name,
                        "tool_source_type": tool_meta.source_type,
                        "tool_source_name": tool_meta.source_name,
                        "content": content,
                    }
                )

            await self._emit("turn_end", turn=turn)
            self._completed_turns = turn
        else:
            log.warning("Agent loop hit max_turns (%d)", settings.max_turns)

        return last_content
