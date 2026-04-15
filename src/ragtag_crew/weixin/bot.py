"""Weixin bot frontend with minimal command support."""

from __future__ import annotations

import logging
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


def _save_current_session(user_id: str, session: AgentSession) -> None:
    save_session(_current_route(user_id).current_session_key, session)


def _delete_current_session(user_id: str) -> None:
    delete_session(_current_route(user_id).current_session_key)


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
        "/session reset - 恢复默认 session"
    )


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


async def _cmd_new(bot: Any, message: Any) -> None:
    session = _get_session(message.user_id)
    if session.is_busy:
        await bot.reply(message, "Please wait — agent is busy.")
        return
    session.reset()
    _delete_current_session(message.user_id)
    await bot.reply(message, "Session cleared.")


async def _cmd_cancel(bot: Any, message: Any) -> None:
    session = _get_session(message.user_id)
    if not session.is_busy:
        await bot.reply(message, "No active task to cancel.")
        return
    session.abort()
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
        await bot.reply(message, _format_session_route(route))
        return

    action = args[0].lower()
    if action == "use":
        if len(args) < 2:
            await bot.reply(message, "Usage: /session use <session_key>")
            return
        if session.is_busy:
            await bot.reply(message, "Please wait — agent is busy.")
            return
        route = set_session_route(
            frontend="weixin",
            peer_id=user_id,
            default_session_key=_default_session_key(user_id),
            session_key=args[1],
        )
        _get_session_by_key(route.current_session_key)
        await bot.reply(message, "Switched session.\n\n" + _format_session_route(route))
        return

    if action == "reset":
        if session.is_busy:
            await bot.reply(message, "Please wait — agent is busy.")
            return
        route = reset_session_route(
            frontend="weixin",
            peer_id=user_id,
            default_session_key=_default_session_key(user_id),
        )
        _get_session_by_key(route.current_session_key)
        await bot.reply(
            message, "Session routing reset.\n\n" + _format_session_route(route)
        )
        return

    await bot.reply(
        message,
        "Usage: /session current | /session use <session_key> | /session reset",
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
    if session.is_busy:
        reply_text = (
            session.render_progress_text()
            if _is_progress_query(text)
            else "Please wait for the current response to finish."
        )
        await bot.reply(message, reply_text)
        return

    collector = TraceCollector(session_key=session_key)
    collector.set_context(
        model=session.model,
        user_input=text,
        tool_preset=session.tool_preset,
        enabled_skills=session.enabled_skills,
        planning_enabled=session.planning_enabled,
    )
    session.subscribe(collector.on_event)

    try:
        try:
            await bot.send_typing(message.user_id)
        except Exception:
            log.debug("Failed to send Weixin typing status", exc_info=True)

        result = await session.prompt(text)
        await bot.reply(message, result.strip() or "Done.")
    except UserAbortedError:
        await bot.reply(message, "已取消当前任务。")
    except Exception as exc:
        log.exception("Weixin prompt() failed")
        await bot.reply(message, f"Error: {exc}")
    finally:
        collector.finalize()
        _save_current_session(message.user_id, session)
        session.unsubscribe(collector.on_event)


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
