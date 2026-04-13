"""TelegramStreamer — streams agent events to Telegram message edits.

Subscribes to AgentSession events and throttles edits to respect
Telegram Bot API rate limits (~1 edit/sec per message).
"""

from __future__ import annotations

import asyncio
import logging
import time

from telegram import Message
from telegram.error import BadRequest, NetworkError, RetryAfter

from ragtag_crew.llm import ToolCall
from ragtag_crew.telegram.html import render_telegram_html

log = logging.getLogger(__name__)

# Telegram limits
# 单条消息编辑频率过低会让流式输出体感很差；1s 仍在 Telegram
# 常见限流经验范围内，且配合后台发送可避免反向阻塞 LLM stream。
_THROTTLE_SECS = 1.0
_MAX_MSG_LEN = 4096
_MAX_RENDER_INPUT = 3500
_FINALIZE_WAIT_SECS = 3.0


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
        self._rate_limited_until: float = 0.0
        self._extra_messages: list[Message] = []
        self._closing = False
        self._transport_closed = False
        self._flush_event = asyncio.Event()
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self._worker_task = asyncio.create_task(
            self._run_worker(), name="ragtag_crew:telegram_stream"
        )

    async def on_event(self, event_type: str, **kwargs) -> None:
        """Event callback — wired to AgentSession.subscribe()."""
        match event_type:
            case "message_update":
                self.buffer += kwargs["delta"]
                self._request_flush()

            case "tool_execution_start":
                tc: ToolCall = kwargs["tool_call"]
                self.buffer += f"\n⏳ {tc.name}({_summarize_args(tc.arguments)})\n"
                self._request_flush()

            case "tool_execution_end":
                # Replace the ⏳ with ✅ inline isn't worth the complexity;
                # the next message_update will push the text forward anyway.
                pass

            case "agent_end":
                self._request_flush()

            case "cancelled":
                self.buffer += "\n\n⚠️ 已取消"
                self._request_flush()

            case "error":
                err = kwargs.get("error", "Unknown error")
                self.buffer += f"\n\n❌ Error: {err}"
                self._request_flush()

    async def finalize(self) -> None:
        """Ensure all buffered text has been sent after the loop ends.

        If the total text exceeds Telegram's 4096 limit, send overflow
        as separate messages.
        """
        if self._transport_closed:
            await self.shutdown()
            return

        self._closing = True
        self._request_flush()
        try:
            await asyncio.wait_for(
                asyncio.shield(self._idle_event.wait()), _FINALIZE_WAIT_SECS
            )
        except asyncio.TimeoutError:
            log.warning(
                "Telegram finalize timed out after %.1fs; dropping pending edits",
                _FINALIZE_WAIT_SECS,
            )

        if not self._transport_closed and self._is_primary_message_synced():
            _, consumed = self._render_window(self.buffer)
            overflow = self.buffer[consumed:]
            while overflow and not self._transport_closed:
                rendered, consumed = self._render_window(overflow)
                raw_chunk = overflow[:consumed]
                overflow = overflow[consumed:]
                try:
                    msg = await self._send_text(rendered, raw_chunk, reply=True)
                    self._extra_messages.append(msg)
                except NetworkError as exc:
                    if self._handle_network_error(exc):
                        break
                    log.warning("reply_text failed: %s", exc)
                    break
                except Exception:
                    log.exception("Failed to send overflow message")
                    break

        await self.shutdown()

    async def shutdown(self) -> None:
        """Stop the background worker and disable future Telegram sends."""
        self._closing = True
        self._transport_closed = True
        self._flush_event.set()
        if not self._worker_task.done():
            self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass

    # -- internal -----------------------------------------------------------

    def _request_flush(self) -> None:
        if self._transport_closed or self._closing and self._worker_task.done():
            return
        self._idle_event.clear()
        self._flush_event.set()

    async def _run_worker(self) -> None:
        try:
            while True:
                await self._flush_event.wait()
                self._flush_event.clear()

                if self._transport_closed:
                    return

                while not self._transport_closed:
                    changed = await self._flush_once()
                    if self._transport_closed:
                        return
                    if self._flush_event.is_set():
                        self._flush_event.clear()
                        continue
                    self._idle_event.set()
                    if self._closing:
                        return
                    if not changed:
                        break
        except Exception:
            log.exception("background telegram flush failed")
        finally:
            self._idle_event.set()

    async def _flush_once(self) -> bool:
        while True:
            if self._transport_closed:
                return False

            rendered, consumed = self._render_window(self.buffer)
            raw_text = self.buffer[:consumed] if self.buffer else "..."
            if raw_text == self._last_sent_text:
                return False

            now = time.monotonic()
            wait_for_rate_limit = max(0.0, self._rate_limited_until - now)
            wait_for_throttle = max(0.0, self._last_edit_time + _THROTTLE_SECS - now)
            sleep_for = max(wait_for_rate_limit, wait_for_throttle)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
                continue

            try:
                await self._send_text(rendered, raw_text, reply=False)
                self._last_sent_text = raw_text
                self._last_edit_time = time.monotonic()
                self._rate_limited_until = 0.0
                return True
            except RetryAfter as exc:
                retry_after = max(float(exc.retry_after), _THROTTLE_SECS)
                log.warning("Rate limited, waiting %s seconds", retry_after)
                self._rate_limited_until = time.monotonic() + retry_after
            except NetworkError as exc:
                if self._handle_network_error(exc):
                    return False
                log.warning("edit_text failed: %s", exc)
                return False
            except BadRequest as exc:
                if "not modified" not in str(exc).lower():
                    log.warning("edit_text failed: %s", exc)
                return False

    def _is_primary_message_synced(self) -> bool:
        _, consumed = self._render_window(self.buffer)
        raw_text = self.buffer[:consumed] if self.buffer else "..."
        return raw_text == self._last_sent_text

    def _handle_network_error(self, exc: NetworkError) -> bool:
        if not self._is_request_shutdown_error(exc):
            return False
        self._transport_closed = True
        log.info("Telegram request transport closed during stream shutdown")
        return True

    def _is_request_shutdown_error(self, exc: BaseException) -> bool:
        needle = "this httpxrequest is not initialized"
        current: BaseException | None = exc
        while current is not None:
            if needle in str(current).lower():
                return True
            current = current.__cause__ or current.__context__
        return False

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
