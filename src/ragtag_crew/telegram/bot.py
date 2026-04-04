"""Telegram bot — message handling, auth, commands, session routing."""

from __future__ import annotations

import logging
from datetime import datetime

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from ragtag_crew.agent import AgentSession
from ragtag_crew.config import settings
from ragtag_crew.external import (
    ensure_external_capabilities_initialized,
    get_mcp_statuses,
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
    "You are a helpful coding assistant.  You have access to tools for "
    "reading, writing, and editing files, as well as running shell commands.  "
    "Use them when the user's request requires interacting with the filesystem."
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
            )
    return _sessions[chat_id]


def _is_authorized(user_id: int) -> bool:
    allowed = settings.get_allowed_user_ids()
    if not allowed:
        return True  # no restriction configured
    return user_id in allowed


# -- handlers ---------------------------------------------------------------

async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(
        "Hello! I'm your AI coding agent.\n"
        f"Model: {settings.default_model}\n"
        f"Tools: {settings.default_tool_preset}\n\n"
        "Send me a message to get started.\n"
        "Commands: /new /model /tools /skills /skill /memory /context /mcp"
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
        return
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    if session.is_busy:
        await update.message.reply_text("Please wait — agent is busy.")
        return
    session.reset()
    delete_session(chat_id)
    await update.message.reply_text("Session cleared.")


async def _cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        return
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    args = (context.args or [])
    if not args:
        await update.message.reply_text(f"Current model: {session.model}\n\nUsage: /model <litellm-model-name>")
        return
    new_model = args[0]
    previous_model = session.model
    try:
        summary = await validate_model(new_model)
    except Exception as exc:
        await update.message.reply_text(
            "Model validation failed; keeping current model.\n"
            f"Current model: {previous_model}\n"
            f"Error: {exc}"
        )
        return

    session.model = new_model
    save_session(chat_id, session)
    await update.message.reply_text(
        f"Model switched to: {new_model}\nValidation reply: {summary}"
    )


async def _cmd_tools(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        return
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    args = (context.args or [])
    if not args:
        names = [t.name for t in session.tools]
        await update.message.reply_text(f"Active tools: {', '.join(names)}\n\nUsage: /tools coding|readonly")
        return
    preset = args[0]
    try:
        new_tools = get_tools_for_preset(preset)
    except KeyError:
        await update.message.reply_text(f"Unknown preset: {preset}\nAvailable: coding, readonly")
        return
    session.tools = new_tools
    session.tool_preset = preset
    save_session(chat_id, session)
    names = [t.name for t in new_tools]
    await update.message.reply_text(f"Tools switched to '{preset}': {', '.join(names)}")


async def _cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        return
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    available = list_skills()
    active = ", ".join(session.enabled_skills) if session.enabled_skills else "(none)"
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
        return
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    args = context.args or []
    if not args:
        active = ", ".join(session.enabled_skills) if session.enabled_skills else "(none)"
        await update.message.reply_text(
            "Usage: /skill use <name> | /skill drop <name> | /skill clear\n"
            f"Active skills: {active}"
        )
        return

    action = args[0].lower()
    if action == "clear":
        session.enabled_skills = []
        save_session(chat_id, session)
        await update.message.reply_text("Cleared all active skills.")
        return

    if len(args) < 2:
        await update.message.reply_text("Usage: /skill use <name> | /skill drop <name>")
        return

    skill_name = args[1]
    try:
        skill = get_skill(skill_name)
    except KeyError:
        await update.message.reply_text(f"Unknown skill: {skill_name}")
        return

    if action == "use":
        if skill.name not in session.enabled_skills:
            session.enabled_skills.append(skill.name)
        save_session(chat_id, session)
        await update.message.reply_text(f"Enabled skill: {skill.name}")
        return

    if action == "drop":
        session.enabled_skills = [name for name in session.enabled_skills if name != skill.name]
        save_session(chat_id, session)
        await update.message.reply_text(f"Disabled skill: {skill.name}")
        return

    await update.message.reply_text("Usage: /skill use <name> | /skill drop <name> | /skill clear")


async def _cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        return

    args = context.args or []
    if not args:
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
        await update.message.reply_text(f"Added memory note to {path.parent.name}/{path.name}")
        return

    if action == "promote":
        target = args[1] if len(args) > 1 else "MEMORY.md"
        try:
            path, count = promote_inbox(target)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return
        location = path.name if path.name == "MEMORY.md" else f"{path.parent.name}/{path.name}"
        await update.message.reply_text(f"Promoted {count} inbox entr{'y' if count == 1 else 'ies'} to {location}")
        return

    await update.message.reply_text(
        "Usage: /memory list | /memory show <name> | /memory add <note> | /memory promote [target]"
    )


async def _cmd_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        return

    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    args = context.args or []
    if not args or args[0].lower() == "show":
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
            await update.message.reply_text(
                "No compaction needed yet.\n\n" + _context_status_text(session)
            )
            return

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


async def _cmd_mcp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(_format_mcp_status_text())


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        return

    chat_id = update.effective_chat.id
    text = update.message.text
    if not text:
        return

    session = _get_session(chat_id)

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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

    return app
