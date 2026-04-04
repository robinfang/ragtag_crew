"""Telegram bot — message handling, auth, commands, session routing."""

from __future__ import annotations

import logging
from datetime import datetime

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from ragtag_crew.agent import AgentSession
from ragtag_crew.config import settings
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
)
from ragtag_crew.model_validation import validate_model
from ragtag_crew.skill_loader import get_skill, list_skills
from ragtag_crew.telegram.stream import TelegramStreamer
from ragtag_crew.telegram.session_store import (
    cleanup_expired_sessions,
    delete_session,
    load_session,
    save_session,
)
from ragtag_crew.tools import get_tools_for_preset

log = logging.getLogger(__name__)

# Session routing: chat_id -> AgentSession
_sessions: dict[int, AgentSession] = {}

# Default system prompt
_SYSTEM_PROMPT = (
    "You are a concise, efficient coding assistant.  "
    "Follow these tool usage rules strictly:\n"
    "\n"
    "1. **Use built-in tools first**: prefer `read`, `write`, `edit`, `delete_file`, `grep`, `find`, `ls` over `bash`. "
    "These tools are faster, safer, and produce cleaner output.\n"
    "2. **`grep`** for content search, **`find`** for file name search, **`ls`** for directory listing. "
    "Do NOT use `bash` with `grep`/`find`/`ls` commands for these tasks.\n"
    "3. **`bash`** is only for operations that built-in tools cannot do: "
    "installing packages, running scripts, git operations, system commands, etc. "
    "Do NOT use `bash` to delete files — use `delete_file` instead.\n"
    "4. **Be concise**: respond in as few words as possible. "
    "Avoid preamble, summaries, and explanations unless asked.\n"
    "5. **Windows environment**: you are running on Windows. "
    "Use forward slashes or backslashes for paths. "
    "For paths outside the working directory, use `bash` with native Windows commands.\n"
    "6. **Batch operations**: make multiple independent tool calls in a single turn when possible."
)


def _get_session(chat_id: int) -> AgentSession:
    if chat_id not in _sessions:
        restored = load_session(chat_id, default_system_prompt=_SYSTEM_PROMPT)
        if restored is not None:
            _sessions[chat_id] = restored
        else:
            _sessions[chat_id] = AgentSession(
                model=settings.default_model,
                tools=get_tools_for_preset(settings.default_tool_preset),
                system_prompt=_SYSTEM_PROMPT,
                tool_preset=settings.default_tool_preset,
                enabled_skills=[],
                browser_mode=settings.browser_mode_default,
            )
    return _sessions[chat_id]


def _is_authorized(user_id: int) -> bool:
    allowed = settings.get_allowed_user_ids()
    if not allowed:
        return True  # no restriction configured
    return user_id in allowed


# -- bot command menu --------------------------------------------------------

_BOT_COMMANDS = [
    BotCommand("new", "清空当前会话"),
    BotCommand("model", "查看 / 切换模型"),
    BotCommand("tools", "查看 / 切换工具预设"),
    BotCommand("skills", "列出可用技能"),
    BotCommand("skill", "启用 / 禁用技能"),
    BotCommand("memory", "记忆管理"),
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
        "输入 / 查看所有可用命令。"
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
    summary = _truncate_reply(session.session_summary, limit=1200) if session.session_summary else "(empty)"
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
    delete_session(chat_id)
    log.info("[chat %s] /new — session cleared", chat_id)
    await update.message.reply_text("Session cleared.")


async def _cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        log.warning("Unauthorized /model from user_id=%s", update.effective_user.id)
        return
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    args = (context.args or [])
    if not args:
        log.debug("[chat %s] /model show — current: %s", chat_id, session.model)
        await update.message.reply_text(f"Current model: {session.model}\n\nUsage: /model <litellm-model-name>")
        return
    new_model = args[0]
    previous_model = session.model
    try:
        summary = await validate_model(new_model)
    except Exception as exc:
        log.warning("[chat %s] /model %s — validation failed: %s", chat_id, new_model, exc)
        await update.message.reply_text(
            "Model validation failed; keeping current model.\n"
            f"Current model: {previous_model}\n"
            f"Error: {exc}"
        )
        return

    session.model = new_model
    save_session(chat_id, session)
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
    args = (context.args or [])
    if not args:
        names = [t.name for t in session.tools]
        log.debug("[chat %s] /tools show — preset: %s, tools: %s", chat_id, session.tool_preset, names)
        await update.message.reply_text(f"Active tools: {', '.join(names)}\n\nUsage: /tools coding|readonly")
        return
    preset = args[0]
    try:
        new_tools = get_tools_for_preset(preset)
    except KeyError:
        log.warning("[chat %s] /tools %s — unknown preset", chat_id, preset)
        await update.message.reply_text(f"Unknown preset: {preset}\nAvailable: coding, readonly")
        return
    session.tools = new_tools
    session.tool_preset = preset
    save_session(chat_id, session)
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
    log.debug("[chat %s] /skills — active: %s, available: %d", chat_id, active, len(available))
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
        active = ", ".join(session.enabled_skills) if session.enabled_skills else "(none)"
        log.debug("[chat %s] /skill show — active: %s", chat_id, active)
        await update.message.reply_text(
            "Usage: /skill use <name> | /skill drop <name> | /skill clear\n"
            f"Active skills: {active}"
        )
        return

    action = args[0].lower()
    if action == "clear":
        session.enabled_skills = []
        save_session(chat_id, session)
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
        log.warning("[chat %s] /skill %s — unknown skill: %s", chat_id, action, skill_name)
        await update.message.reply_text(f"Unknown skill: {skill_name}")
        return

    if action == "use":
        if skill.name not in session.enabled_skills:
            session.enabled_skills.append(skill.name)
        save_session(chat_id, session)
        log.info("[chat %s] /skill use %s", chat_id, skill.name)
        await update.message.reply_text(f"Enabled skill: {skill.name}")
        return

    if action == "drop":
        session.enabled_skills = [name for name in session.enabled_skills if name != skill.name]
        save_session(chat_id, session)
        log.info("[chat %s] /skill drop %s", chat_id, skill.name)
        await update.message.reply_text(f"Disabled skill: {skill.name}")
        return

    await update.message.reply_text("Usage: /skill use <name> | /skill drop <name> | /skill clear")


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
            "Usage: /memory list | /memory show <name> | /memory add <note> | /memory promote [target]"
        )
        return

    action = args[0].lower()
    if action == "list":
        files = list_memory_files()
        if not files:
            await update.message.reply_text("Memory files:\n- (none)")
            return
        await update.message.reply_text("Memory files:\n" + "\n".join(f"- {name}" for name in files))
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

    if action == "add":
        note = " ".join(args[1:]).strip()
        if not note:
            await update.message.reply_text("Usage: /memory add <note>")
            return
        path = append_memory_note(note)
        log.info("[chat %s] /memory add -> %s/%s", update.effective_chat.id, path.parent.name, path.name)
        await update.message.reply_text(f"Added memory note to {path.parent.name}/{path.name}")
        return

    if action == "promote":
        target = args[1] if len(args) > 1 else "MEMORY.md"
        try:
            path, count = promote_inbox(target)
        except ValueError as exc:
            log.warning("[chat %s] /memory promote %s — failed: %s", update.effective_chat.id, target, exc)
            await update.message.reply_text(str(exc))
            return
        location = path.name if path.name == "MEMORY.md" else f"{path.parent.name}/{path.name}"
        log.info("[chat %s] /memory promote %d entries -> %s", update.effective_chat.id, count, location)
        await update.message.reply_text(f"Promoted {count} inbox entr{'y' if count == 1 else 'ies'} to {location}")
        return

    await update.message.reply_text(
        "Usage: /memory list | /memory show <name> | /memory add <note> | /memory promote [target]"
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

        changed = session.compact(force=True)
        save_session(chat_id, session)
        if not changed:
            log.debug("[chat %s] /context compress — no change", chat_id)
            await update.message.reply_text(
                "No compaction needed yet.\n\n" + _context_status_text(session)
            )
            return

        log.info("[chat %s] /context compress — done, %d messages kept", chat_id, len(session.messages))
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
    if runtime.attached_target.startswith("http://") or runtime.attached_target.startswith("ws://") or runtime.attached_target.startswith("wss://"):
        attached_path = "manual-cdp"
        attached_target_text = runtime.attached_target
        attached_connect_hint = "Run /browser connect to attach via the configured CDP URL."
    elif runtime.attached_target == "auto-connect":
        attached_path = "auto-connect"
        attached_target_text = "discover a running Chrome/Edge automatically"
        attached_connect_hint = "Run /browser connect to let agent-browser auto-discover Chrome/Edge."
    else:
        attached_path = "not-configured"
        attached_target_text = "set BROWSER_ATTACHED_CDP_URL or enable BROWSER_ATTACHED_AUTO_CONNECT"
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
        await update.message.reply_text("MCP capabilities reloaded.\n\n" + _format_mcp_status_text())
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
            await update.message.reply_text(
                "Usage: /browser mode isolated|attached"
            )
            return
        mode = args[1].lower()
        if mode not in {"isolated", "attached"}:
            await update.message.reply_text("Usage: /browser mode isolated|attached")
            return
        if mode == "attached" and not settings.browser_attached_enabled:
            await update.message.reply_text("Attached browser mode is disabled in configuration.")
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
        save_session(chat_id, session)
        log.info("[chat %s] /browser mode -> %s", chat_id, mode)
        await update.message.reply_text(
            f"Browser mode switched to: {mode}\n\n" + _format_browser_status_text(session)
        )
        return

    if action == "confirm-attached":
        session.browser_attached_confirmed = True
        save_session(chat_id, session)
        log.info("[chat %s] /browser confirm-attached", chat_id)
        await update.message.reply_text(
            "Attached browser confirmed for this session.\n\n" + _format_browser_status_text(session)
        )
        return

    if action == "revoke-attached":
        session.browser_attached_confirmed = False
        if session.browser_mode == "attached":
            session.browser_mode = "isolated"
        save_session(chat_id, session)
        log.info("[chat %s] /browser revoke-attached", chat_id)
        await update.message.reply_text(
            "Attached browser confirmation revoked.\n\n" + _format_browser_status_text(session)
        )
        return

    if action == "connect":
        if settings.browser_attached_require_confirmation and not session.browser_attached_confirmed:
            await update.message.reply_text(
                "Attached browser connect requires explicit confirmation first. "
                "Run /browser confirm-attached before connecting."
            )
            return
        ok, detail = await connect_attached_browser()
        await initialize_external_capabilities(force=True)
        log.info("[chat %s] /browser connect — %s", chat_id, "ok" if ok else "failed")
        prefix = "Attached browser connected." if ok else "Attached browser connect failed."
        await update.message.reply_text(
            f"{prefix}\n{detail}\n\n" + _format_browser_status_text(session)
        )
        return

    if action == "disconnect":
        detail = disconnect_attached_browser()
        await initialize_external_capabilities(force=True)
        log.info("[chat %s] /browser disconnect", chat_id)
        await update.message.reply_text(detail + "\n\n" + _format_browser_status_text(session))
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

    session = _get_session(chat_id)
    log.debug("[chat %s] prompt: %.80s", chat_id, text)

    if session.is_busy:
        await update.message.reply_text("Please wait for the current response to finish.")
        return

    placeholder = await update.message.reply_text("Thinking...")
    streamer = TelegramStreamer(placeholder)
    session.subscribe(streamer.on_event)

    try:
        await session.prompt(text)
    except Exception as exc:
        log.exception("prompt() failed")
        try:
            await placeholder.edit_text(f"Error: {exc}")
        except Exception:
            pass
    finally:
        save_session(chat_id, session)
        await streamer.finalize()
        session.unsubscribe(streamer.on_event)


# -- application builder ---------------------------------------------------

async def _register_commands(app: Application) -> None:
    await app.bot.set_my_commands(_BOT_COMMANDS)
    log.info("bot command menu registered (%d commands)", len(_BOT_COMMANDS))


def build_app() -> Application:
    """Construct the python-telegram-bot Application."""
    # Force-import tool modules so they self-register.
    import ragtag_crew.tools.file_tools  # noqa: F401
    import ragtag_crew.tools.search_tools  # noqa: F401
    import ragtag_crew.tools.shell_tools  # noqa: F401

    cleanup_expired_sessions()
    ensure_external_capabilities_initialized()

    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("new", _cmd_new))
    app.add_handler(CommandHandler("model", _cmd_model))
    app.add_handler(CommandHandler("tools", _cmd_tools))
    app.add_handler(CommandHandler("skills", _cmd_skills))
    app.add_handler(CommandHandler("skill", _cmd_skill))
    app.add_handler(CommandHandler("memory", _cmd_memory))
    app.add_handler(CommandHandler("context", _cmd_context))
    app.add_handler(CommandHandler("mcp", _cmd_mcp))
    app.add_handler(CommandHandler("ext", _cmd_ext))
    app.add_handler(CommandHandler("browser", _cmd_browser))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

    app.post_init = _register_commands

    return app
