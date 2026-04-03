"""AgentSession — self-built agent loop.

Drives the  LLM <-> tool-call  cycle and emits events that the
Telegram streaming layer can subscribe to.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Awaitable

from ragtag_crew.config import settings
from ragtag_crew.errors import LLMChunkTimeoutError, LLMTimeoutError, TurnTimeoutError
from ragtag_crew.llm import stream_chat, LLMResponse, ToolCall
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
        agent_end(content=str)
        error(error=Exception)
    """

    def __init__(
        self,
        model: str,
        tools: list[Tool],
        system_prompt: str = "",
        tool_preset: str = "coding",
    ):
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.tool_preset = tool_preset
        self.messages: list[dict[str, Any]] = []

        self._callbacks: list[EventCallback] = []
        self._abort_event = asyncio.Event()
        self._busy = False

    # -- public state -------------------------------------------------------

    @property
    def is_busy(self) -> bool:
        return self._busy

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

    # -- core loop ----------------------------------------------------------

    async def prompt(self, text: str) -> str:
        """Send user text and run the agent loop until completion.

        Returns the final assistant text (also emitted via events).
        """
        if self._busy:
            raise RuntimeError("Session is already processing a prompt")

        self._busy = True
        self._abort_event.clear()
        self.messages.append({"role": "user", "content": text})

        await self._emit("agent_start")
        final_content = ""

        try:
            final_content = await asyncio.wait_for(
                self._run_loop(),
                timeout=settings.turn_timeout,
            )
        except (LLMTimeoutError, LLMChunkTimeoutError):
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
        finally:
            self._busy = False
            await self._emit("agent_end", content=final_content)

        return final_content

    async def _run_loop(self) -> str:
        tool_schemas = build_tool_schemas(self.tools) if self.tools else None
        last_content = ""

        for turn in range(1, settings.max_turns + 1):
            if self._abort_event.is_set():
                break

            await self._emit("turn_start", turn=turn)
            await self._emit("message_start")

            # Stream LLM call
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
                await self._emit("message_end", content=response.content)
                self._append_assistant_message(response)
                last_content = response.content
                raise

            await self._emit("message_end", content=response.content)

            self._append_assistant_message(response)

            last_content = response.content

            # If abort was requested during streaming, keep the partial text
            # that already arrived, but do not continue with tool execution.
            if self._abort_event.is_set():
                await self._emit("turn_end", turn=turn)
                break

            # No tool calls → done
            if not response.tool_calls:
                await self._emit("turn_end", turn=turn)
                break

            # Execute each tool call
            for tc in response.tool_calls:
                if self._abort_event.is_set():
                    break

                await self._emit("tool_execution_start", tool_call=tc)
                result = await self._execute_tool(tc)
                await self._emit("tool_execution_end", tool_call=tc, result=result)

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

            await self._emit("turn_end", turn=turn)
        else:
            # Exceeded max_turns
            log.warning("Agent loop hit max_turns (%d)", settings.max_turns)

        return last_content

    async def _on_text_delta(self, delta: str) -> None:
        await self._emit("message_update", delta=delta)

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

    def _build_messages(self) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        msgs.extend(self.messages)
        return msgs

    async def _execute_tool(self, tc: ToolCall) -> str:
        try:
            tool = get_tool(tc.name)
        except KeyError:
            return f"ERROR: unknown tool '{tc.name}'"
        try:
            return await tool.execute(**tc.arguments)
        except Exception as exc:
            log.exception("Tool %s execution failed", tc.name)
            return f"ERROR: {type(exc).__name__}: {exc}"
