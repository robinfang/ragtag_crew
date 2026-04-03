"""Telegram bot — message handling, auth, commands, session routing."""

from __future__ import annotations

import logging

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
from ragtag_crew.telegram.stream import TelegramStreamer
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
        _sessions[chat_id] = AgentSession(
            model=settings.default_model,
            tools=get_tools_for_preset(settings.default_tool_preset),
            system_prompt=_SYSTEM_PROMPT,
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
        "Commands: /new /model /tools"
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
    session.model = new_model
    await update.message.reply_text(f"Model switched to: {new_model}")


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
    names = [t.name for t in new_tools]
    await update.message.reply_text(f"Tools switched to '{preset}': {', '.join(names)}")


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
        await streamer.finalize()
        session.unsubscribe(streamer.on_event)


# -- application builder ---------------------------------------------------

def build_app() -> Application:
    """Construct the python-telegram-bot Application."""
    # Force-import tool modules so they self-register.
    import ragtag_crew.tools.file_tools  # noqa: F401
    import ragtag_crew.tools.search_tools  # noqa: F401
    import ragtag_crew.tools.shell_tools  # noqa: F401

    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("new", _cmd_new))
    app.add_handler(CommandHandler("model", _cmd_model))
    app.add_handler(CommandHandler("tools", _cmd_tools))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

    return app
