"""Telegram bot — message handling, auth, commands, session routing."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime

from telegram import BotCommand, Update
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest

from ragtag_crew.agent import AgentSession
from ragtag_crew.config import settings
from ragtag_crew.errors import UserAbortedError
from ragtag_crew.external.browser_agent import (
    connect_attached_browser,
    disconnect_attached_browser,
    get_browser_runtime_state,
)
from ragtag_crew.external import (
    ensure_external_capabilities_initialized,
    get_capability_statuses,
    get_browser_statuses,
    get_mcp_statuses,
    initialize_external_capabilities,
)
from ragtag_crew.memory_store import (
    append_memory_note,
    list_memory_files,
    promote_inbox,
    read_memory_file,
    read_memory_index,
    search_memory,
)
from ragtag_crew.model_validation import validate_model
from ragtag_crew.prompts import DEFAULT_SYSTEM_PROMPT
from ragtag_crew.session_routes import (
    SessionRoute,
    detect_session_source,
    get_session_route,
    reset_session_route,
    set_session_route,
)
from ragtag_crew.session_store import SessionKey
from ragtag_crew.skill_loader import get_skill, list_skills
from ragtag_crew.telegram.stream import TelegramStreamer
from ragtag_crew.trace import TraceCollector
from ragtag_crew.session_store import (
    cleanup_expired_sessions,
    delete_session,
    list_sessions,
    load_session,
    save_session,
)
from ragtag_crew.tools import ensure_builtin_tools_registered, get_tools_for_preset

log = logging.getLogger(__name__)

# Session routing: session_key -> AgentSession
_sessions: dict[SessionKey, AgentSession] = {}


@dataclass(slots=True)
class TelegramRuntimeHealth:
    started_at: float
    last_poll_success_at: float
    last_message_activity_at: float
    last_error_at: float | None = None
    last_error_repr: str = ""
    consecutive_poll_failures: int = 0

    @classmethod
    def create(cls) -> "TelegramRuntimeHealth":
        now = time.monotonic()
        return cls(
            started_at=now,
            last_poll_success_at=now,
            last_message_activity_at=now,
        )

    def mark_poll_success(self) -> None:
        self.last_poll_success_at = time.monotonic()
        self.consecutive_poll_failures = 0

    def mark_message_activity(self) -> None:
        self.last_message_activity_at = time.monotonic()

    def mark_poll_error(self, exc: BaseException) -> None:
        self.last_error_at = time.monotonic()
        self.last_error_repr = f"{exc.__class__.__name__}: {exc}"
        self.consecutive_poll_failures += 1

    def snapshot(self) -> dict[str, float | int | str | None]:
        return {
            "poll_stale_secs": round(time.monotonic() - self.last_poll_success_at, 1),
            "message_idle_secs": round(
                time.monotonic() - self.last_message_activity_at, 1
            ),
            "consecutive_poll_failures": self.consecutive_poll_failures,
            "last_error": self.last_error_repr or None,
        }


class HealthAwareHTTPXRequest(HTTPXRequest):
    def __init__(self, *, health: TelegramRuntimeHealth, **kwargs) -> None:
        super().__init__(**kwargs)
        self._health = health

    async def do_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            result = await super().do_request(*args, **kwargs)
        except Exception as exc:
            self._health.mark_poll_error(exc)
            raise
        self._health.mark_poll_success()
        return result


def _build_request_kwargs(*, read_timeout: float) -> dict[str, object]:
    httpx_kwargs: dict[str, object] = {}
    if settings.telegram_disable_env_proxy:
        httpx_kwargs["trust_env"] = False

    return {
        "connect_timeout": settings.telegram_connect_timeout,
        "read_timeout": read_timeout,
        "write_timeout": settings.telegram_write_timeout,
        "pool_timeout": settings.telegram_pool_timeout,
        "http_version": "1.1",
        "proxy": settings.telegram_proxy or None,
        "httpx_kwargs": httpx_kwargs,
    }


async def _handle_application_error(
    update: object | None, context: ContextTypes.DEFAULT_TYPE
) -> None:
    app = getattr(context, "application", None)
    bot_data = getattr(app, "bot_data", {}) if app is not None else {}
    health = bot_data.get("telegram_runtime_health")
    extra = health.snapshot() if isinstance(health, TelegramRuntimeHealth) else {}
    if isinstance(context.error, NetworkError):
        log.warning("Telegram network error; runtime=%s", extra, exc_info=context.error)
        return
    log.exception(
        "Unhandled telegram application error; runtime=%s",
        extra,
        exc_info=context.error,
    )


async def _monitor_telegram_health(app: Application) -> None:
    health = app.bot_data.get("telegram_runtime_health")
    if not isinstance(health, TelegramRuntimeHealth):
        return

    interval_secs = max(5, min(30, settings.telegram_health_stale_seconds // 4 or 5))
    while True:
        await asyncio.sleep(interval_secs)
        stale_for = time.monotonic() - health.last_poll_success_at
        if stale_for < settings.telegram_health_stale_seconds:
            continue
        if health.consecutive_poll_failures <= 0:
            continue
        log.error(
            "Telegram polling unhealthy for %.1fs; stopping application for supervisor restart. runtime=%s",
            stale_for,
            health.snapshot(),
        )
        app.stop_running()
        return


async def _post_init(app: Application) -> None:
    await _register_commands(app)
    app.bot_data["telegram_health_monitor_task"] = asyncio.create_task(
        _monitor_telegram_health(app),
        name="ragtag_crew:telegram_health_monitor",
    )


async def _post_stop(app: Application) -> None:
    for session in _sessions.values():
        if session.is_busy:
            session.abort()

    prompt_tasks = list(app.bot_data.pop("active_prompt_tasks", set()))
    if prompt_tasks:
        results = await asyncio.gather(*prompt_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                log.warning("Telegram prompt task stopped with error: %s", result)

    streamers = list(app.bot_data.pop("active_streamers", []))
    if streamers:
        results = await asyncio.gather(
            *(streamer.shutdown() for streamer in streamers), return_exceptions=True
        )
        for result in results:
            if isinstance(result, Exception):
                log.warning("Failed to stop telegram streamer cleanly: %s", result)

    task = app.bot_data.pop("telegram_health_monitor_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _default_session_key(chat_id: int) -> int:
    return chat_id


def _current_route(chat_id: int) -> SessionRoute:
    return get_session_route(
        frontend="telegram",
        peer_id=chat_id,
        default_session_key=_default_session_key(chat_id),
    )


def _get_session_by_key(session_key: SessionKey) -> AgentSession:
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


def _get_session(chat_id: int) -> AgentSession:
    return _get_session_by_key(_current_route(chat_id).current_session_key)


def _save_current_session(chat_id: int, session: AgentSession) -> None:
    save_session(_current_route(chat_id).current_session_key, session)


def _delete_current_session(chat_id: int) -> None:
    delete_session(_current_route(chat_id).current_session_key)


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


def _is_authorized(user_id: int) -> bool:
    allowed = settings.get_allowed_user_ids()
    if not allowed:
        return True  # no restriction configured
    return user_id in allowed


# -- bot command menu --------------------------------------------------------

_BOT_COMMANDS = [
    BotCommand("help", "显示帮助"),
    BotCommand("new", "清空当前会话"),
    BotCommand("cancel", "取消当前回复"),
    BotCommand("plan", "规划模式 on / off"),
    BotCommand("sessions", "列出最近 session"),
    BotCommand("session", "查看 / 切换当前 session"),
    BotCommand("model", "查看 / 切换模型"),
    BotCommand("tools", "查看 / 切换工具预设"),
    BotCommand("skills", "列出可用技能"),
    BotCommand("skill", "启用 / 禁用技能"),
    BotCommand("memory", "记忆管理"),
    BotCommand("prompt", "会话提示与保护内容"),
    BotCommand("context", "查看 / 压缩上下文"),
    BotCommand("mcp", "MCP 服务器状态"),
    BotCommand("ext", "外部能力状态"),
    BotCommand("browser", "浏览器控制"),
]

_REGISTERED_COMMAND_NAMES = frozenset(c.command for c in _BOT_COMMANDS)


# -- handlers ---------------------------------------------------------------


async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /start from user_id=%s", update.effective_user.id)
        return
    log.debug("/start from chat_id=%s", update.effective_chat.id)
    await update.message.reply_text(
        "Hello! I'm your AI coding agent.\n"
        f"Model: {settings.default_model}\n"
        f"Tools: {settings.default_tool_preset}\n\n"
        "Send me a message to get started.\n"
        "输入 /help 查看常用命令，输入 / 查看所有可用命令。"
    )


async def _cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /help from user_id=%s", update.effective_user.id)
        return
    await update.message.reply_text(_help_text())


async def _cmd_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /sessions from user_id=%s", update.effective_user.id)
        return
    await update.message.reply_text(_format_saved_sessions())


async def _cmd_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /session from user_id=%s", update.effective_user.id)
        return

    chat_id = update.effective_chat.id
    route = _current_route(chat_id)
    session = _get_session(chat_id)
    args = context.args or []

    if not args or args[0].lower() == "current":
        await update.message.reply_text(
            _with_session_usage(_format_session_route(route))
        )
        return

    action = args[0].lower()
    if action == "use":
        if len(args) < 2:
            await update.message.reply_text(
                _with_session_usage("Usage error: missing session target.")
            )
            return
        if session.is_busy:
            await update.message.reply_text(
                _with_session_usage("Please wait — agent is busy.")
            )
            return
        try:
            target_session_key = _resolve_session_target(args[1])
        except ValueError as exc:
            await update.message.reply_text(_with_session_usage(str(exc)))
            return
        route = set_session_route(
            frontend="telegram",
            peer_id=chat_id,
            default_session_key=_default_session_key(chat_id),
            session_key=target_session_key,
        )
        _get_session_by_key(route.current_session_key)
        await update.message.reply_text(
            _with_session_usage("Switched session.\n\n" + _format_session_route(route))
        )
        return

    if action == "reset":
        if session.is_busy:
            await update.message.reply_text(
                _with_session_usage("Please wait — agent is busy.")
            )
            return
        route = reset_session_route(
            frontend="telegram",
            peer_id=chat_id,
            default_session_key=_default_session_key(chat_id),
        )
        _get_session_by_key(route.current_session_key)
        await update.message.reply_text(
            _with_session_usage(
                "Session routing reset.\n\n" + _format_session_route(route)
            )
        )
        return

    await update.message.reply_text(
        _with_session_usage("Usage error: unknown /session action.")
    )


def _truncate_reply(text: str, limit: int = 3500) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _format_summary_time(timestamp: float | None) -> str:
    if not timestamp:
        return "never"
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


def _context_status_text(session: AgentSession) -> str:
    summary = (
        _truncate_reply(session.session_summary, limit=1200)
        if session.session_summary
        else "(empty)"
    )
    return (
        "Context status:\n"
        f"Messages kept: {len(session.messages)}\n"
        f"Recent window target: {settings.session_summary_recent_messages}\n"
        f"Auto-compact trigger: {settings.session_summary_trigger_messages}\n"
        f"Summary updated at: {_format_summary_time(session.summary_updated_at)}\n\n"
        "Session summary:\n"
        f"{summary}\n\n"
        "Usage: /context show | /context compress"
    )


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
        lines.append(
            f"{index}. {record.session_key} | {detect_session_source(record.session_key)} | "
            f"{record.model or '(unknown)'} | {record.tool_preset or '(unknown)'} | "
            f"{_format_summary_time(record.last_active_at)}"
        )
    return "\n".join(lines)


def _resolve_session_target(target: str) -> SessionKey:
    records = list_sessions()
    for record in records:
        if record.session_key == target:
            return target

    if target.isdecimal():
        index = int(target)
        if index < 1 or index > len(records):
            # Telegram session keys are often long numeric IDs. Keep those usable.
            if len(target) <= 4:
                raise ValueError(f"Session index out of range: {target}")
            return target
        return records[index - 1].session_key

    return target


async def _cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /new from user_id=%s", update.effective_user.id)
        return
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    if session.is_busy:
        await update.message.reply_text("Please wait — agent is busy.")
        return
    session.reset()
    _delete_current_session(chat_id)
    log.info("[chat %s] /new — session cleared", chat_id)
    await update.message.reply_text("Session cleared.")


async def _cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /cancel from user_id=%s", update.effective_user.id)
        return
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    if not session.is_busy:
        await update.message.reply_text("No active task to cancel.")
        return
    session.abort()
    log.info("[chat %s] /cancel — abort signalled", chat_id)
    await update.message.reply_text("已发送取消信号。")


async def _cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /plan from user_id=%s", update.effective_user.id)
        return
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    args = context.args or []

    if not args:
        status = "ON" if session.planning_enabled else "OFF"
        mode = "Plan" if session.planning_enabled else "Build"
        log.debug("[chat %s] /plan show — %s", chat_id, status)
        await update.message.reply_text(
            f"Current mode: {mode}\nPlanning: {status}\n\nUsage: /plan on | /plan off"
        )
        return

    action = args[0].lower()
    if action == "on":
        session.planning_enabled = True
        _save_current_session(chat_id, session)
        log.info("[chat %s] /plan on", chat_id)
        await update.message.reply_text(
            "Plan mode ON — will output numbered plan before acting on non-trivial tasks."
        )
    elif action == "off":
        session.planning_enabled = False
        clear_pending_plan = getattr(session, "clear_pending_plan", None)
        if callable(clear_pending_plan):
            clear_pending_plan()
        _save_current_session(chat_id, session)
        log.info("[chat %s] /plan off", chat_id)
        await update.message.reply_text(
            "Build mode ON — will proceed directly without planning."
        )
    else:
        await update.message.reply_text("Usage: /plan on | /plan off")


async def _cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /model from user_id=%s", update.effective_user.id)
        return
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    args = context.args or []
    if not args:
        models = settings.get_available_models()
        lines = [f"Current model: {session.model}"]
        if models:
            lines.append("")
            lines.append("Available models:")
            for m in models:
                marker = " ←" if m == session.model else ""
                lines.append(f"• {m}{marker}")
        lines.append("")
        lines.append("Usage: /model <model-name>")
        text = "\n".join(lines)
        log.debug("[chat %s] /model show — current: %s", chat_id, session.model)
        await update.message.reply_text(text)
        return
    new_model = args[0]
    previous_model = session.model
    try:
        summary = await validate_model(new_model)
    except Exception as exc:
        log.warning(
            "[chat %s] /model %s — validation failed: %s", chat_id, new_model, exc
        )
        await update.message.reply_text(
            "Model validation failed; keeping current model.\n"
            f"Current model: {previous_model}\n"
            f"Error: {exc}"
        )
        return

    session.model = new_model
    _save_current_session(chat_id, session)
    log.info("[chat %s] /model %s -> %s", chat_id, previous_model, new_model)
    await update.message.reply_text(
        f"Model switched to: {new_model}\nValidation reply: {summary}"
    )


async def _cmd_tools(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /tools from user_id=%s", update.effective_user.id)
        return
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    args = context.args or []
    if not args:
        names = [t.name for t in session.tools]
        log.debug(
            "[chat %s] /tools show — preset: %s, tools: %s",
            chat_id,
            session.tool_preset,
            names,
        )
        await update.message.reply_text(
            f"Active tools: {', '.join(names)}\n\nUsage: /tools coding|readonly"
        )
        return
    preset = args[0]
    try:
        new_tools = get_tools_for_preset(preset)
    except KeyError:
        log.warning("[chat %s] /tools %s — unknown preset", chat_id, preset)
        await update.message.reply_text(
            f"Unknown preset: {preset}\nAvailable: coding, readonly"
        )
        return
    session.tools = new_tools
    session.tool_preset = preset
    _save_current_session(chat_id, session)
    names = [t.name for t in new_tools]
    log.info("[chat %s] /tools -> %s", chat_id, preset)
    await update.message.reply_text(f"Tools switched to '{preset}': {', '.join(names)}")


async def _cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /skills from user_id=%s", update.effective_user.id)
        return
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    available = list_skills()
    active = ", ".join(session.enabled_skills) if session.enabled_skills else "(none)"
    log.debug(
        "[chat %s] /skills — active: %s, available: %d", chat_id, active, len(available)
    )
    if not available:
        await update.message.reply_text(
            f"Active skills: {active}\nNo local skills found in {settings.skills_dir}"
        )
        return

    lines = [f"Active skills: {active}", "", "Available skills:"]
    for skill in available:
        summary = f" - {skill.summary}" if skill.summary else ""
        lines.append(f"- {skill.name}{summary}")
    await update.message.reply_text("\n".join(lines))


async def _cmd_skill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /skill from user_id=%s", update.effective_user.id)
        return
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    args = context.args or []
    if not args:
        active = (
            ", ".join(session.enabled_skills) if session.enabled_skills else "(none)"
        )
        log.debug("[chat %s] /skill show — active: %s", chat_id, active)
        await update.message.reply_text(
            "Usage: /skill use <name> | /skill drop <name> | /skill clear\n"
            f"Active skills: {active}"
        )
        return

    action = args[0].lower()
    if action == "clear":
        session.enabled_skills = []
        _save_current_session(chat_id, session)
        log.info("[chat %s] /skill clear", chat_id)
        await update.message.reply_text("Cleared all active skills.")
        return

    if len(args) < 2:
        await update.message.reply_text("Usage: /skill use <name> | /skill drop <name>")
        return

    skill_name = args[1]
    try:
        skill = get_skill(skill_name)
    except KeyError:
        log.warning(
            "[chat %s] /skill %s — unknown skill: %s", chat_id, action, skill_name
        )
        await update.message.reply_text(f"Unknown skill: {skill_name}")
        return

    if action == "use":
        if skill.name not in session.enabled_skills:
            session.enabled_skills.append(skill.name)
        _save_current_session(chat_id, session)
        log.info("[chat %s] /skill use %s", chat_id, skill.name)
        await update.message.reply_text(f"Enabled skill: {skill.name}")
        return

    if action == "drop":
        session.enabled_skills = [
            name for name in session.enabled_skills if name != skill.name
        ]
        _save_current_session(chat_id, session)
        log.info("[chat %s] /skill drop %s", chat_id, skill.name)
        await update.message.reply_text(f"Disabled skill: {skill.name}")
        return

    await update.message.reply_text(
        "Usage: /skill use <name> | /skill drop <name> | /skill clear"
    )


async def _cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /memory from user_id=%s", update.effective_user.id)
        return

    args = context.args or []
    if not args:
        log.debug("[chat %s] /memory show", update.effective_chat.id)
        index = read_memory_index()
        files = list_memory_files()
        file_lines = [f"- {name}" for name in files] or ["- (none)"]
        index_text = _truncate_reply(index) if index else "(empty)"
        file_block = "\n".join(file_lines)
        await update.message.reply_text(
            "Memory index (MEMORY.md):\n"
            f"{index_text}\n\n"
            "Memory files:\n"
            f"{file_block}\n\n"
            "Usage: /memory list | /memory show <name> | /memory search <query> | /memory add <note> | /memory promote [target]"
        )
        return

    action = args[0].lower()
    if action == "list":
        files = list_memory_files()
        if not files:
            await update.message.reply_text("Memory files:\n- (none)")
            return
        await update.message.reply_text(
            "Memory files:\n" + "\n".join(f"- {name}" for name in files)
        )
        return

    if action == "show":
        if len(args) < 2:
            await update.message.reply_text("Usage: /memory show <index|file>")
            return
        target = args[1]
        try:
            content = read_memory_file(target)
        except (FileNotFoundError, ValueError) as exc:
            await update.message.reply_text(str(exc))
            return
        content = _truncate_reply(content) if content else "(empty)"
        await update.message.reply_text(content)
        return

    if action == "search":
        query = " ".join(args[1:]).strip()
        if not query:
            await update.message.reply_text("Usage: /memory search <query>")
            return
        try:
            hits = search_memory(query)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return
        if not hits:
            await update.message.reply_text(f"No memory results found for: {query}")
            return
        lines = [f"Memory search results for: {query}"]
        for index, hit in enumerate(hits, start=1):
            lines.append(f"{index}. {hit.file_name}:{hit.line}")
            lines.append(f"   {hit.snippet}")
        await update.message.reply_text("\n".join(lines))
        return

    if action == "add":
        note = " ".join(args[1:]).strip()
        if not note:
            await update.message.reply_text("Usage: /memory add <note>")
            return
        path = append_memory_note(note)
        log.info(
            "[chat %s] /memory add -> %s/%s",
            update.effective_chat.id,
            path.parent.name,
            path.name,
        )
        await update.message.reply_text(
            f"Added memory note to {path.parent.name}/{path.name}"
        )
        return

    if action == "promote":
        target = args[1] if len(args) > 1 else "MEMORY.md"
        try:
            path, count = promote_inbox(target)
        except ValueError as exc:
            log.warning(
                "[chat %s] /memory promote %s — failed: %s",
                update.effective_chat.id,
                target,
                exc,
            )
            await update.message.reply_text(str(exc))
            return
        location = (
            path.name if path.name == "MEMORY.md" else f"{path.parent.name}/{path.name}"
        )
        log.info(
            "[chat %s] /memory promote %d entries -> %s",
            update.effective_chat.id,
            count,
            location,
        )
        await update.message.reply_text(
            f"Promoted {count} inbox entr{'y' if count == 1 else 'ies'} to {location}"
        )
        return

    await update.message.reply_text(
        "Usage: /memory list | /memory show <name> | /memory search <query> | /memory add <note> | /memory promote [target]"
    )


async def _cmd_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /prompt from user_id=%s", update.effective_user.id)
        return

    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    args = context.args or []

    if not args or args[0].lower() == "show":
        session_prompt = session.session_prompt or "(empty)"
        protected = session.protected_content or "(empty)"
        await update.message.reply_text(
            "Session prompt:\n"
            f"{_truncate_reply(session_prompt)}\n\n"
            "Protected content:\n"
            f"{_truncate_reply(protected)}\n\n"
            "Usage: /prompt show | /prompt set <text> | /prompt clear | /prompt protect <text> | /prompt unprotect"
        )
        return

    action = args[0].lower()
    if action == "set":
        text = " ".join(args[1:]).strip()
        if not text:
            await update.message.reply_text("Usage: /prompt set <text>")
            return
        session.session_prompt = text
        _save_current_session(chat_id, session)
        await update.message.reply_text("Session prompt updated.")
        return

    if action == "clear":
        session.session_prompt = ""
        _save_current_session(chat_id, session)
        await update.message.reply_text("Session prompt cleared.")
        return

    if action == "protect":
        text = " ".join(args[1:]).strip()
        if not text:
            await update.message.reply_text("Usage: /prompt protect <text>")
            return
        session.protected_content = text
        _save_current_session(chat_id, session)
        await update.message.reply_text("Protected content updated.")
        return

    if action == "unprotect":
        session.protected_content = ""
        _save_current_session(chat_id, session)
        await update.message.reply_text("Protected content cleared.")
        return

    await update.message.reply_text(
        "Usage: /prompt show | /prompt set <text> | /prompt clear | /prompt protect <text> | /prompt unprotect"
    )


async def _cmd_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /context from user_id=%s", update.effective_user.id)
        return

    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    args = context.args or []
    if not args or args[0].lower() == "show":
        log.debug("[chat %s] /context show", chat_id)
        await update.message.reply_text(_context_status_text(session))
        return

    action = args[0].lower()
    if action == "compress":
        if session.is_busy:
            await update.message.reply_text("Please wait — agent is busy.")
            return

        changed = await session.compact(force=True)
        _save_current_session(chat_id, session)
        if not changed:
            log.debug("[chat %s] /context compress — no change", chat_id)
            await update.message.reply_text(
                "No compaction needed yet.\n\n" + _context_status_text(session)
            )
            return

        log.info(
            "[chat %s] /context compress — done, %d messages kept",
            chat_id,
            len(session.messages),
        )
        await update.message.reply_text(
            "Context compacted.\n\n" + _context_status_text(session)
        )
        return

    await update.message.reply_text("Usage: /context show | /context compress")


def _format_mcp_status_text() -> str:
    statuses = get_mcp_statuses()
    if not statuses:
        return (
            "No MCP servers configured.\n"
            f"Create {settings.mcp_servers_file} from mcp_servers.example.json."
        )

    lines = ["MCP status:"]
    for status in statuses:
        state = "ready" if status.ready else "not ready"
        lines.append(f"- {status.key}: {state}")
        if status.detail:
            lines.append(f"  detail: {status.detail}")
        if status.tool_names:
            lines.append(f"  tools: {', '.join(status.tool_names)}")
    return "\n".join(lines)


def _capability_state_text(status) -> str:  # type: ignore[no-untyped-def]
    if status.ready:
        return "ready"
    if status.detail in {"disabled", "windows-only"}:
        return "disabled"
    return "error"


def _format_capability_status_text() -> str:
    statuses = get_capability_statuses()
    if not statuses:
        return "External capabilities not initialized yet."

    lines = ["External capabilities:"]
    for status in statuses:
        lines.append(f"- {status.key}: {_capability_state_text(status)}")
        lines.append(f"  kind: {status.kind}")
        if status.detail:
            lines.append(f"  detail: {status.detail}")
        if status.tool_names:
            lines.append(f"  tools: {', '.join(status.tool_names)}")
    lines.append("")
    lines.append("Usage: /ext show | /ext reload")
    return "\n".join(lines)


def _format_browser_status_text(session: AgentSession) -> str:
    runtime = get_browser_runtime_state(session_mode=session.browser_mode)
    statuses = {status.key: status for status in get_browser_statuses()}
    isolated = statuses.get("browser-isolated")
    attached = statuses.get("browser-attached")
    isolated_state = _capability_state_text(isolated) if isolated else "unknown"
    attached_state = _capability_state_text(attached) if attached else "unknown"
    isolated_detail = isolated.detail if isolated else ""
    attached_detail = attached.detail if attached else ""
    if (
        runtime.attached_target.startswith("http://")
        or runtime.attached_target.startswith("ws://")
        or runtime.attached_target.startswith("wss://")
    ):
        attached_path = "manual-cdp"
        attached_target_text = runtime.attached_target
        attached_connect_hint = (
            "Run /browser connect to attach via the configured CDP URL."
        )
    elif runtime.attached_target == "auto-connect":
        attached_path = "auto-connect"
        attached_target_text = "discover a running Chrome/Edge automatically"
        attached_connect_hint = (
            "Run /browser connect to let agent-browser auto-discover Chrome/Edge."
        )
    else:
        attached_path = "not-configured"
        attached_target_text = (
            "set BROWSER_ATTACHED_CDP_URL or enable BROWSER_ATTACHED_AUTO_CONNECT"
        )
        attached_connect_hint = "Configure a CDP URL for stable attach, or enable auto-connect for convenience."
    return (
        "Browser status:\n"
        f"Session mode: {runtime.session_mode}\n"
        f"Default mode: {runtime.default_mode}\n"
        f"Command: {runtime.command}\n"
        f"Attached confirmed: {'yes' if session.browser_attached_confirmed else 'no'}\n"
        f"Allowed domains: {settings.browser_allowed_domains or '(all)'}\n"
        f"Isolated: {isolated_state}\n"
        f"  detail: {isolated_detail or f'profile={runtime.isolated_profile_dir}'}\n"
        f"Attached: {attached_state}\n"
        f"  path: {attached_path}\n"
        f"  target: {attached_target_text}\n"
        f"  detail: {attached_detail or runtime.attached_target}\n"
        f"  connected: {'yes' if runtime.attached_connected else 'no'}\n\n"
        f"Connect hint: {attached_connect_hint}\n"
        "Manual CDP is steadier; auto-connect is more convenient but depends on local browser discovery.\n\n"
        "Usage: /browser status | /browser mode isolated|attached | /browser confirm-attached | /browser revoke-attached | /browser connect | /browser disconnect"
    )


async def _cmd_ext(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /ext from user_id=%s", update.effective_user.id)
        return

    args = context.args or []
    action = args[0].lower() if args else "show"
    if action == "show":
        log.debug("[chat %s] /ext show", update.effective_chat.id)
        await update.message.reply_text(_format_capability_status_text())
        return

    if action == "reload":
        await initialize_external_capabilities(force=True)
        log.info("[chat %s] /ext reload", update.effective_chat.id)
        await update.message.reply_text(
            "External capabilities reloaded.\n\n" + _format_capability_status_text()
        )
        return

    await update.message.reply_text("Usage: /ext show | /ext reload")


async def _cmd_mcp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /mcp from user_id=%s", update.effective_user.id)
        return
    args = context.args or []
    if args and args[0].lower() == "reload":
        await initialize_external_capabilities(force=True)
        log.info("[chat %s] /mcp reload", update.effective_chat.id)
        await update.message.reply_text(
            "MCP capabilities reloaded.\n\n" + _format_mcp_status_text()
        )
        return
    log.debug("[chat %s] /mcp show", update.effective_chat.id)
    await update.message.reply_text(_format_mcp_status_text())


async def _cmd_browser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /browser from user_id=%s", update.effective_user.id)
        return

    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    args = context.args or []
    action = args[0].lower() if args else "status"

    if action == "status":
        log.debug("[chat %s] /browser status", chat_id)
        await update.message.reply_text(_format_browser_status_text(session))
        return

    if action == "mode":
        if len(args) < 2:
            await update.message.reply_text("Usage: /browser mode isolated|attached")
            return
        mode = args[1].lower()
        if mode not in {"isolated", "attached"}:
            await update.message.reply_text("Usage: /browser mode isolated|attached")
            return
        if mode == "attached" and not settings.browser_attached_enabled:
            await update.message.reply_text(
                "Attached browser mode is disabled in configuration."
            )
            return
        if (
            mode == "attached"
            and settings.browser_attached_require_confirmation
            and not session.browser_attached_confirmed
        ):
            await update.message.reply_text(
                "Attached browser mode requires explicit confirmation first. "
                "Run /browser confirm-attached before switching."
            )
            return
        session.browser_mode = mode
        _save_current_session(chat_id, session)
        log.info("[chat %s] /browser mode -> %s", chat_id, mode)
        await update.message.reply_text(
            f"Browser mode switched to: {mode}\n\n"
            + _format_browser_status_text(session)
        )
        return

    if action == "confirm-attached":
        session.browser_attached_confirmed = True
        _save_current_session(chat_id, session)
        log.info("[chat %s] /browser confirm-attached", chat_id)
        await update.message.reply_text(
            "Attached browser confirmed for this session.\n\n"
            + _format_browser_status_text(session)
        )
        return

    if action == "revoke-attached":
        session.browser_attached_confirmed = False
        if session.browser_mode == "attached":
            session.browser_mode = "isolated"
        _save_current_session(chat_id, session)
        log.info("[chat %s] /browser revoke-attached", chat_id)
        await update.message.reply_text(
            "Attached browser confirmation revoked.\n\n"
            + _format_browser_status_text(session)
        )
        return

    if action == "connect":
        if (
            settings.browser_attached_require_confirmation
            and not session.browser_attached_confirmed
        ):
            await update.message.reply_text(
                "Attached browser connect requires explicit confirmation first. "
                "Run /browser confirm-attached before connecting."
            )
            return
        ok, detail = await connect_attached_browser()
        await initialize_external_capabilities(force=True)
        log.info("[chat %s] /browser connect — %s", chat_id, "ok" if ok else "failed")
        prefix = (
            "Attached browser connected." if ok else "Attached browser connect failed."
        )
        await update.message.reply_text(
            f"{prefix}\n{detail}\n\n" + _format_browser_status_text(session)
        )
        return

    if action == "disconnect":
        detail = disconnect_attached_browser()
        await initialize_external_capabilities(force=True)
        log.info("[chat %s] /browser disconnect", chat_id)
        await update.message.reply_text(
            detail + "\n\n" + _format_browser_status_text(session)
        )
        return

    await update.message.reply_text(
        "Usage: /browser status | /browser mode isolated|attached | /browser confirm-attached | /browser revoke-attached | /browser connect | /browser disconnect"
    )


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized message from user_id=%s", update.effective_user.id)
        return

    chat_id = update.effective_chat.id
    text = update.message.text
    if not text:
        return

    app = getattr(context, "application", None)
    bot_data = getattr(app, "bot_data", {}) if app is not None else {}
    health = bot_data.get("telegram_runtime_health")
    if isinstance(health, TelegramRuntimeHealth):
        health.mark_message_activity()

    route = _current_route(chat_id)
    session_key = route.current_session_key
    session = _get_session_by_key(session_key)
    log.debug("[chat %s] prompt: %.80s", chat_id, text)

    if session.is_busy:
        if _is_progress_query(text):
            await update.message.reply_text(session.render_progress_text())
        else:
            await update.message.reply_text(
                "Please wait for the current response to finish."
            )
        return

    placeholder = await update.message.reply_text("Thinking...")
    streamer = TelegramStreamer(placeholder)
    if app is not None:
        app.bot_data.setdefault("active_streamers", []).append(streamer)
    collector = TraceCollector(session_key=session_key)
    collector.set_context(
        model=session.model,
        user_input=text,
        tool_preset=session.tool_preset,
        enabled_skills=session.enabled_skills,
        planning_enabled=session.planning_enabled,
    )
    session.subscribe(streamer.on_event)
    session.subscribe(collector.on_event)

    async def _run_prompt() -> None:
        try:
            await session.prompt(text)
        except UserAbortedError:
            pass
        except Exception as exc:
            log.exception("prompt() failed")
            try:
                await placeholder.edit_text(f"Error: {exc}")
            except Exception:
                pass
        finally:
            collector.finalize()
            save_session(session_key, session)
            await streamer.finalize()
            if app is not None:
                streamers = app.bot_data.get("active_streamers", [])
                if streamer in streamers:
                    streamers.remove(streamer)
            session.unsubscribe(streamer.on_event)
            session.unsubscribe(collector.on_event)

    if app is None or not hasattr(app, "create_task"):
        await _run_prompt()
        return

    task = app.create_task(_run_prompt(), name=f"ragtag_crew:telegram_prompt:{chat_id}")
    active_prompt_tasks = app.bot_data.setdefault("active_prompt_tasks", set())
    active_prompt_tasks.add(task)
    task.add_done_callback(active_prompt_tasks.discard)


# -- application builder ---------------------------------------------------


async def _register_commands(app: Application) -> None:
    try:
        await app.bot.set_my_commands(_BOT_COMMANDS)
        log.info("bot command menu registered (%d commands)", len(_BOT_COMMANDS))
    except Exception:
        log.warning(
            "failed to register bot command menu, continuing without it", exc_info=True
        )


def build_app() -> Application:
    """Construct the python-telegram-bot Application."""
    ensure_builtin_tools_registered()

    cleanup_expired_sessions()
    ensure_external_capabilities_initialized()

    health = TelegramRuntimeHealth.create()
    request = HTTPXRequest(
        **_build_request_kwargs(read_timeout=settings.telegram_read_timeout)
    )
    get_updates_request = HealthAwareHTTPXRequest(
        health=health,
        **_build_request_kwargs(read_timeout=settings.telegram_read_timeout),
    )

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .request(request)
        .get_updates_request(get_updates_request)
        .build()
    )
    app.bot_data["telegram_runtime_health"] = health
    app.bot_data["active_streamers"] = []
    app.bot_data["active_prompt_tasks"] = set()

    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("help", _cmd_help))
    app.add_handler(CommandHandler("new", _cmd_new))
    app.add_handler(CommandHandler("cancel", _cmd_cancel))
    app.add_handler(CommandHandler("plan", _cmd_plan))
    app.add_handler(CommandHandler("sessions", _cmd_sessions))
    app.add_handler(CommandHandler("session", _cmd_session))
    app.add_handler(CommandHandler("model", _cmd_model))
    app.add_handler(CommandHandler("tools", _cmd_tools))
    app.add_handler(CommandHandler("skills", _cmd_skills))
    app.add_handler(CommandHandler("skill", _cmd_skill))
    app.add_handler(CommandHandler("memory", _cmd_memory))
    app.add_handler(CommandHandler("prompt", _cmd_prompt))
    app.add_handler(CommandHandler("context", _cmd_context))
    app.add_handler(CommandHandler("mcp", _cmd_mcp))
    app.add_handler(CommandHandler("ext", _cmd_ext))
    app.add_handler(CommandHandler("browser", _cmd_browser))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    app.add_error_handler(_handle_application_error)

    app.post_init = _post_init
    app.post_stop = _post_stop

    return app
