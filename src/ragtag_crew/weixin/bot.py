"""Weixin bot frontend with background execution support."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ragtag_crew.agent import AgentSession
from ragtag_crew.config import settings
from ragtag_crew.errors import UserAbortedError
from ragtag_crew.external import ensure_external_capabilities_initialized
from ragtag_crew.prompts import DEFAULT_SYSTEM_PROMPT
from ragtag_crew.session_routes import (
    SessionRoute,
    detect_session_source,
    get_session_route,
    reset_session_route,
    set_session_route,
)
from ragtag_crew.session_store import (
    cleanup_expired_sessions,
    delete_session,
    list_sessions,
    load_session,
    save_session,
)
from ragtag_crew.tools import ensure_builtin_tools_registered, get_tools_for_preset
from ragtag_crew.trace import TraceCollector

if TYPE_CHECKING:
    from weixin_bot import WeixinBot
    from weixin_bot.types import IncomingMessage

log = logging.getLogger(__name__)

_sessions: dict[str, AgentSession] = {}
_active_prompt_tasks: dict[str, asyncio.Task[Any]] = {}

_STATUS_HEARTBEAT_SECS = 20.0
_STATUS_THROTTLE_SECS = 10.0


def _default_session_key(user_id: str) -> str:
    return f"weixin:{user_id}"


def _current_route(user_id: str) -> SessionRoute:
    return get_session_route(
        frontend="weixin",
        peer_id=user_id,
        default_session_key=_default_session_key(user_id),
    )


def _is_authorized(user_id: str) -> bool:
    allowed = settings.get_weixin_allowed_user_ids()
    if not allowed:
        return True
    return user_id in allowed


def _is_progress_query(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    keywords = (
        "progress",
        "status",
        "update",
        "进展",
        "进度",
        "怎么样了",
        "咋样了",
        "好了没",
        "做完没",
    )
    return any(keyword in normalized for keyword in keywords)


def _get_session_by_key(session_key: str) -> AgentSession:
    if session_key not in _sessions:
        restored = load_session(
            session_key, default_system_prompt=DEFAULT_SYSTEM_PROMPT
        )
        if restored is not None:
            _sessions[session_key] = restored
        else:
            _sessions[session_key] = AgentSession(
                model=settings.default_model,
                tools=get_tools_for_preset(settings.default_tool_preset),
                system_prompt=DEFAULT_SYSTEM_PROMPT,
                tool_preset=settings.default_tool_preset,
                enabled_skills=[],
                browser_mode=settings.browser_mode_default,
            )
    return _sessions[session_key]


def _get_session(user_id: str) -> AgentSession:
    return _get_session_by_key(_current_route(user_id).current_session_key)


def _get_active_prompt_task(session_key: str) -> asyncio.Task[Any] | None:
    task = _active_prompt_tasks.get(session_key)
    if task is None:
        return None
    if task.done():
        _active_prompt_tasks.pop(session_key, None)
        return None
    return task


def _save_current_session(user_id: str, session: AgentSession) -> None:
    save_session(_current_route(user_id).current_session_key, session)


def _delete_current_session(user_id: str) -> None:
    delete_session(_current_route(user_id).current_session_key)


def _split_weixin_text(text: str, *, limit: int = 1800) -> list[str]:
    normalized = text.strip() or "Done."
    if len(normalized) <= limit:
        return [normalized]

    chunks: list[str] = []
    current = ""
    for paragraph in normalized.split("\n\n"):
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(paragraph) <= limit:
            current = paragraph
            continue
        for line in paragraph.splitlines():
            candidate = line if not current else f"{current}\n{line}"
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ""
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line
    if current:
        chunks.append(current)
    return chunks or [normalized]


async def _send_weixin_text(bot: Any, user_id: str, text: str) -> None:
    for chunk in _split_weixin_text(text):
        await bot.send(user_id, chunk)


class WeixinProgressNotifier:
    def __init__(self, bot: Any, user_id: str, session: AgentSession) -> None:
        self.bot = bot
        self.user_id = user_id
        self.session = session
        self._last_status_text = ""
        self._last_status_sent_at = 0.0
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self._send_status("开始处理，请稍候。")
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def close(self) -> None:
        if self._heartbeat_task is None:
            return
        self._heartbeat_task.cancel()
        try:
            await self._heartbeat_task
        except asyncio.CancelledError:
            pass
        self._heartbeat_task = None

    async def on_event(self, event_type: str, **kwargs: Any) -> None:
        if event_type != "tool_execution_start":
            return
        tool_call = kwargs.get("tool_call")
        tool_name = getattr(tool_call, "name", "unknown")
        await self._send_status(f"正在执行工具: {tool_name}", force=True)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(_STATUS_HEARTBEAT_SECS)
            if not self.session.is_busy:
                return
            await self._send_status(self.session.render_progress_text())

    async def _send_status(self, text: str, *, force: bool = False) -> None:
        message = text.strip()
        if not message:
            return
        now = time.monotonic()
        if not force and now - self._last_status_sent_at < _STATUS_THROTTLE_SECS:
            return
        if force and message == self._last_status_text and now - self._last_status_sent_at < 3:
            return
        try:
            await self.bot.send(self.user_id, message)
            self._last_status_text = message
            self._last_status_sent_at = now
        except Exception:
            log.debug("Failed to send Weixin status update", exc_info=True)


async def _run_session_prompt_in_background(
    bot: Any,
    message: Any,
    session: AgentSession,
    session_key: str,
    text: str,
) -> None:
    collector = TraceCollector(session_key=session_key)
    collector.set_context(
        model=session.model,
        user_input=text,
        tool_preset=session.tool_preset,
        enabled_skills=session.enabled_skills,
        planning_enabled=session.planning_enabled,
    )
    notifier = WeixinProgressNotifier(bot, message.user_id, session)
    session.subscribe(collector.on_event)
    session.subscribe(notifier.on_event)

    try:
        await notifier.start()
        try:
            await bot.send_typing(message.user_id)
        except Exception:
            log.debug("Failed to send Weixin typing status", exc_info=True)

        result = await session.prompt(text)
        await _send_weixin_text(bot, message.user_id, result.strip() or "Done.")
    except asyncio.CancelledError:
        session.abort()
        await _send_weixin_text(bot, message.user_id, "已取消当前任务。")
        raise
    except UserAbortedError:
        await _send_weixin_text(bot, message.user_id, "已取消当前任务。")
    except Exception as exc:
        log.exception("Weixin prompt() failed")
        await _send_weixin_text(bot, message.user_id, f"Error: {exc}")
    finally:
        await notifier.close()
        collector.finalize()
        _save_current_session(message.user_id, session)
        session.unsubscribe(notifier.on_event)
        session.unsubscribe(collector.on_event)
        current_task = asyncio.current_task()
        active_task = _active_prompt_tasks.get(session_key)
        if active_task is current_task or active_task is None or active_task.done():
            _active_prompt_tasks.pop(session_key, None)


def _help_text() -> str:
    return (
        "可用命令:\n"
        "/help - 显示帮助\n"
        "/new - 清空当前绑定的会话\n"
        "/cancel - 取消当前回复\n"
        "/plan - 查看或切换规划模式\n"
        "/sessions - 列出最近保存的 session\n"
        "/session current - 查看当前绑定\n"
        "/session use <session_key> - 切换到指定 session\n"
        "/session use <index> - 按 /sessions 序号切换\n"
        "/session reset - 恢复默认 session"
    )


def _session_usage_text() -> str:
    return (
        "Usage:\n"
        "/session current\n"
        "/session use <session_key>\n"
        "/session use <index>\n"
        "/session reset"
    )


def _with_session_usage(text: str) -> str:
    return f"{text}\n\n{_session_usage_text()}"


def _format_session_route(route: SessionRoute) -> str:
    mode = "overridden" if route.is_overridden else "default"
    return (
        f"Current session: {route.current_session_key}\n"
        f"Default session: {route.default_session_key}\n"
        f"Mode: {mode}"
    )


def _format_saved_sessions() -> str:
    records = list_sessions()
    if not records:
        return "No saved sessions."

    lines = ["Saved sessions:"]
    for index, record in enumerate(records, start=1):
        timestamp = "never"
        if record.last_active_at:
            timestamp = datetime.fromtimestamp(record.last_active_at).isoformat(
                timespec="seconds"
            )
        lines.append(
            f"{index}. {record.session_key} | {detect_session_source(record.session_key)} | "
            f"{record.model or '(unknown)'} | {record.tool_preset or '(unknown)'} | {timestamp}"
        )
    return "\n".join(lines)


def _resolve_session_target(target: str) -> str:
    records = list_sessions()
    for record in records:
        if record.session_key == target:
            return target

    if target.isdecimal():
        index = int(target)
        if index < 1 or index > len(records):
            if len(target) <= 4:
                raise ValueError(f"Session index out of range: {target}")
            return target
        return records[index - 1].session_key

    return target


async def _cmd_new(bot: Any, message: Any) -> None:
    session = _get_session(message.user_id)
    if session.is_busy or _get_active_prompt_task(_current_route(message.user_id).current_session_key):
        await bot.reply(message, "Please wait — agent is busy.")
        return
    session.reset()
    _delete_current_session(message.user_id)
    await bot.reply(message, "Session cleared.")


async def _cmd_cancel(bot: Any, message: Any) -> None:
    session = _get_session(message.user_id)
    session_key = _current_route(message.user_id).current_session_key
    task = _get_active_prompt_task(session_key)
    if not session.is_busy and task is None:
        await bot.reply(message, "No active task to cancel.")
        return
    session.abort()
    if task is not None:
        task.cancel()
    await bot.reply(message, "已发送取消信号。")


async def _cmd_plan(bot: Any, message: Any, args: list[str]) -> None:
    session = _get_session(message.user_id)
    user_id = message.user_id

    if not args:
        status = "ON" if session.planning_enabled else "OFF"
        mode = "Plan" if session.planning_enabled else "Build"
        await bot.reply(
            message,
            f"Current mode: {mode}\nPlanning: {status}\n\nUsage: /plan on | /plan off",
        )
        return

    action = args[0].lower()
    if action == "on":
        session.planning_enabled = True
        _save_current_session(user_id, session)
        await bot.reply(message, "Plan mode ON — 输出编号计划后再执行。")
        return
    if action == "off":
        session.planning_enabled = False
        clear_pending_plan = getattr(session, "clear_pending_plan", None)
        if callable(clear_pending_plan):
            clear_pending_plan()
        _save_current_session(user_id, session)
        await bot.reply(message, "Build mode ON — 直接执行，不输出计划。")
        return
    await bot.reply(message, "Usage: /plan on | /plan off")


async def _cmd_help(bot: Any, message: Any) -> None:
    await bot.reply(message, _help_text())


async def _cmd_sessions(bot: Any, message: Any) -> None:
    await bot.reply(message, _format_saved_sessions())


async def _cmd_session(bot: Any, message: Any, args: list[str]) -> None:
    user_id = message.user_id
    route = _current_route(user_id)
    session = _get_session(user_id)

    if not args or args[0].lower() == "current":
        await bot.reply(message, _with_session_usage(_format_session_route(route)))
        return

    action = args[0].lower()
    if action == "use":
        if len(args) < 2:
            await bot.reply(
                message, _with_session_usage("Usage error: missing session target.")
            )
            return
        if session.is_busy or _get_active_prompt_task(route.current_session_key):
            await bot.reply(
                message, _with_session_usage("Please wait — agent is busy.")
            )
            return
        try:
            target_session_key = _resolve_session_target(args[1])
        except ValueError as exc:
            await bot.reply(message, _with_session_usage(str(exc)))
            return
        route = set_session_route(
            frontend="weixin",
            peer_id=user_id,
            default_session_key=_default_session_key(user_id),
            session_key=target_session_key,
        )
        _get_session_by_key(route.current_session_key)
        await bot.reply(
            message,
            _with_session_usage("Switched session.\n\n" + _format_session_route(route)),
        )
        return

    if action == "reset":
        if session.is_busy or _get_active_prompt_task(route.current_session_key):
            await bot.reply(
                message, _with_session_usage("Please wait — agent is busy.")
            )
            return
        route = reset_session_route(
            frontend="weixin",
            peer_id=user_id,
            default_session_key=_default_session_key(user_id),
        )
        _get_session_by_key(route.current_session_key)
        await bot.reply(
            message,
            _with_session_usage(
                "Session routing reset.\n\n" + _format_session_route(route)
            ),
        )
        return

    await bot.reply(
        message,
        _with_session_usage("Usage error: unknown /session action."),
    )


async def _handle_command(bot: Any, message: Any, text: str) -> bool:
    parts = text.split()
    if not parts:
        return False

    command = parts[0].lower()
    args = parts[1:]
    if command == "/help":
        await _cmd_help(bot, message)
        return True
    if command == "/new":
        await _cmd_new(bot, message)
        return True
    if command == "/cancel":
        await _cmd_cancel(bot, message)
        return True
    if command == "/plan":
        await _cmd_plan(bot, message, args)
        return True
    if command == "/sessions":
        await _cmd_sessions(bot, message)
        return True
    if command == "/session":
        await _cmd_session(bot, message, args)
        return True
    return False


async def handle_incoming_message(bot: Any, message: Any) -> None:
    text = (message.text or "").strip()
    if not text:
        return
    if not _is_authorized(message.user_id):
        log.warning("Unauthorized Weixin message from user_id=%s", message.user_id)
        return
    if await _handle_command(bot, message, text):
        return

    session = _get_session(message.user_id)
    route = _current_route(message.user_id)
    session_key = route.current_session_key
    active_task = _get_active_prompt_task(session_key)
    if session.is_busy or active_task is not None:
        reply_text = (
            session.render_progress_text()
            if session.is_busy and _is_progress_query(text)
            else (
                "任务已提交，正在启动。"
                if active_task is not None and not session.is_busy and _is_progress_query(text)
                else "当前任务仍在执行，请等待完成或发送 /cancel。"
            )
        )
        await bot.reply(message, reply_text)
        return

    task = asyncio.create_task(
        _run_session_prompt_in_background(bot, message, session, session_key, text)
    )
    _active_prompt_tasks[session_key] = task
    await bot.reply(message, "已收到，开始处理。可随时发送 /cancel 或直接询问进度。")


def run_weixin_frontend() -> int:
    from weixin_bot import WeixinBot

    ensure_builtin_tools_registered()
    cleanup_expired_sessions()
    ensure_external_capabilities_initialized()

    credentials_path = Path(settings.weixin_credentials_path).expanduser()
    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    bot = WeixinBot(
        token_path=str(credentials_path),
        on_error=lambda error: log.warning("Weixin SDK error: %s", error),
    )

    @bot.on_message
    async def _on_message(message: IncomingMessage) -> None:
        await handle_incoming_message(bot, message)

    log.info("Weixin frontend started; waiting for messages...")
    try:
        bot.login()
        bot.run()
        return 0
    except KeyboardInterrupt:
        log.info("Weixin frontend interrupted; shutting down.")
        return 0
    except Exception:
        log.exception("Weixin frontend crashed.")
        return 1
