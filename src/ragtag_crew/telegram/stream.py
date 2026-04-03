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

log = logging.getLogger(__name__)

# Telegram limits
_THROTTLE_SECS = 1.5
_MAX_MSG_LEN = 4096


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

    async def on_event(self, event_type: str, **kwargs) -> None:
        """Event callback — wired to AgentSession.subscribe()."""
        match event_type:
            case "message_update":
                self.buffer += kwargs["delta"]
                await self._maybe_flush()

            case "tool_execution_start":
                tc: ToolCall = kwargs["tool_call"]
                self.buffer += f"\n⏳ {tc.name}({_summarize_args(tc.arguments)})\n"
                await self._maybe_flush()

            case "tool_execution_end":
                # Replace the ⏳ with ✅ inline isn't worth the complexity;
                # the next message_update will push the text forward anyway.
                pass

            case "agent_end":
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
        overflow = self.buffer[_MAX_MSG_LEN:]
        while overflow:
            chunk = overflow[:_MAX_MSG_LEN]
            overflow = overflow[_MAX_MSG_LEN:]
            try:
                msg = await self.message.reply_text(chunk)
                self._extra_messages.append(msg)
            except Exception:
                log.exception("Failed to send overflow message")

    # -- internal -----------------------------------------------------------

    async def _maybe_flush(self) -> None:
        now = time.monotonic()
        if now - self._last_edit_time >= _THROTTLE_SECS:
            await self._flush()

    async def _flush(self) -> None:
        text = self.buffer[:_MAX_MSG_LEN] if self.buffer else "..."
        if text == self._last_sent_text:
            return

        try:
            await self.message.edit_text(text)
            self._last_sent_text = text
            self._last_edit_time = time.monotonic()
        except RetryAfter as exc:
            log.warning("Rate limited, waiting %s seconds", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
            await self._flush()  # retry once
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():
                log.warning("edit_text failed: %s", exc)
