"""TelegramStreamer — streams agent events to Telegram message edits.

Subscribes to AgentSession events and throttles edits to respect
Telegram Bot API rate limits (~1 edit/sec per message).
"""

from __future__ import annotations

import asyncio
import logging
import time

from telegram import Message
from telegram.error import BadRequest, RetryAfter

from ragtag_crew.llm import ToolCall
from ragtag_crew.telegram.html import render_telegram_html

log = logging.getLogger(__name__)

# Telegram limits
# 单条消息编辑频率过低会让流式输出体感很差；1s 仍在 Telegram
# 常见限流经验范围内，且配合后台发送可避免反向阻塞 LLM stream。
_THROTTLE_SECS = 1.0
_MAX_MSG_LEN = 4096
_MAX_RENDER_INPUT = 3500


def _summarize_args(args: dict) -> str:
    """One-liner summary of tool arguments for display."""
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)


class TelegramStreamer:
    """Receives agent events and mirrors them into a Telegram message.

    Usage::

        placeholder = await update.message.reply_text("Thinking...")
        streamer = TelegramStreamer(placeholder)
        session.subscribe(streamer.on_event)
        await session.prompt(text)
        await streamer.finalize()
        session.unsubscribe(streamer.on_event)
    """

    def __init__(self, message: Message) -> None:
        self.message = message
        self.buffer = ""
        self._last_edit_time: float = 0.0
        self._last_sent_text: str = ""
        self._extra_messages: list[Message] = []
        self._flush_task: asyncio.Task[None] | None = None
        self._flush_lock = asyncio.Lock()

    async def on_event(self, event_type: str, **kwargs) -> None:
        """Event callback — wired to AgentSession.subscribe()."""
        match event_type:
            case "message_update":
                self.buffer += kwargs["delta"]
                self._maybe_flush()

            case "tool_execution_start":
                tc: ToolCall = kwargs["tool_call"]
                self.buffer += f"\n⏳ {tc.name}({_summarize_args(tc.arguments)})\n"
                self._maybe_flush()

            case "tool_execution_end":
                # Replace the ⏳ with ✅ inline isn't worth the complexity;
                # the next message_update will push the text forward anyway.
                pass

            case "agent_end":
                await self._flush()

            case "cancelled":
                self.buffer += "\n\n⚠️ 已取消"
                await self._flush()

            case "error":
                err = kwargs.get("error", "Unknown error")
                self.buffer += f"\n\n❌ Error: {err}"
                await self._flush()

    async def finalize(self) -> None:
        """Ensure all buffered text has been sent after the loop ends.

        If the total text exceeds Telegram's 4096 limit, send overflow
        as separate messages.
        """
        await self._flush()

        # Handle overflow beyond the first message
        _, consumed = self._render_window(self.buffer)
        overflow = self.buffer[consumed:]
        while overflow:
            rendered, consumed = self._render_window(overflow)
            raw_chunk = overflow[:consumed]
            overflow = overflow[consumed:]
            try:
                msg = await self._send_text(rendered, raw_chunk, reply=True)
                self._extra_messages.append(msg)
            except Exception:
                log.exception("Failed to send overflow message")

    # -- internal -----------------------------------------------------------

    def _maybe_flush(self) -> None:
        now = time.monotonic()
        if now - self._last_edit_time < _THROTTLE_SECS:
            return
        if self._flush_task and not self._flush_task.done():
            return
        self._flush_task = asyncio.create_task(self._flush_in_background())

    async def _flush_in_background(self) -> None:
        try:
            await self._flush()
        except Exception:
            log.exception("background telegram flush failed")
        finally:
            current = asyncio.current_task()
            if self._flush_task is current:
                self._flush_task = None

    async def _flush(self) -> None:
        retry_after: float | None = None
        while True:
            if retry_after is not None:
                await asyncio.sleep(retry_after)
                retry_after = None

            async with self._flush_lock:
                rendered, consumed = self._render_window(self.buffer)
                raw_text = self.buffer[:consumed] if self.buffer else "..."
                if raw_text == self._last_sent_text:
                    return

                try:
                    await self._send_text(rendered, raw_text, reply=False)
                    self._last_sent_text = raw_text
                    self._last_edit_time = time.monotonic()
                    return
                except RetryAfter as exc:
                    log.warning("Rate limited, waiting %s seconds", exc.retry_after)
                    retry_after = exc.retry_after
                except BadRequest as exc:
                    if "not modified" not in str(exc).lower():
                        log.warning("edit_text failed: %s", exc)
                    return

    def _render_window(self, raw_text: str) -> tuple[str, int]:
        if not raw_text:
            return "...", 0

        consumed = min(len(raw_text), _MAX_RENDER_INPUT)
        while consumed > 0:
            candidate = raw_text[:consumed]
            rendered = render_telegram_html(candidate)
            if len(rendered) <= _MAX_MSG_LEN:
                return rendered, consumed
            consumed -= max(1, consumed // 8)

        return "...", 0

    async def _send_text(self, rendered: str, raw_text: str, *, reply: bool) -> Message:
        sender = self.message.reply_text if reply else self.message.edit_text

        try:
            return await sender(
                rendered,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            lowered = str(exc).lower()
            if (
                "parse entities" not in lowered
                and "can't parse entities" not in lowered
            ):
                raise
            return await sender(raw_text or "...", disable_web_page_preview=True)
