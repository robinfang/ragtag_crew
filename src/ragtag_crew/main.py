"""Entry point for ragtag_crew."""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import sys
import time
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from telegram.error import InvalidToken

from ragtag_crew.config import settings
from ragtag_crew.skill_loader import get_skill, list_skills

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"

_DESCRIPTION = (
    "草台班子 · 单用户自托管 Telegram AI 编程助手\n"
    "\n"
    "配置走 .env，LLM 通过 litellm 统一接入，支持本地工具、MCP、OpenAPI 工具和浏览器自动化。"
)

_EPILOG = (
    "配置文件：\n"
    "  .env                       主配置（从 .env.example 复制）\n"
    "  PROJECT.md                 项目背景，每轮自动注入\n"
    "  USER.local.md              用户偏好（gitignore，可选）\n"
    "  mcp_servers.local.json     MCP server 配置（可选）\n"
    "  openapi_tools.local.json   固定 OpenAPI 工具配置（可选）\n"
    "\n"
    "示例：\n"
    "  ragtag-crew                     正常启动\n"
    "  ragtag-crew --dev               开发模式（自动重启 + DEBUG 日志）\n"
    "  ragtag-crew --repl              本地终端交互（不连 Telegram）\n"
    "  ragtag-crew --check             检查配置是否完整\n"
    "  ragtag-crew --working-dir /tmp  指定工作目录\n"
    "  ragtag-crew --model openai/GLM-5-Turbo --tools readonly"
)


def _get_version() -> str:
    try:
        return f"ragtag-crew {version('ragtag-crew')}"
    except Exception:
        return "ragtag-crew (version unknown)"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ragtag-crew",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=_get_version())
    parser.add_argument("--working-dir", help="工作目录（覆盖 .env）")
    parser.add_argument("--model", help="默认 LLM 模型（覆盖 .env）")
    parser.add_argument(
        "--tools",
        choices=["coding", "readonly"],
        help="工具预设（覆盖 .env）",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别（覆盖 .env）",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="开发模式：DEBUG 日志 + 文件变动自动重启",
    )
    parser.add_argument(
        "--repl",
        action="store_true",
        help="本地终端交互模式，不连接 Telegram",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="校验配置完整性，不启动 bot",
    )
    parser.add_argument(
        "--history-list",
        action="store_true",
        help="列出已保存的会话历史",
    )
    parser.add_argument(
        "--history",
        type=int,
        help="查看指定 chat_id 的会话历史摘要",
    )
    return parser


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    return build_arg_parser().parse_args(list(argv) if argv is not None else None)


def _setup_logging() -> None:
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    fmt = logging.Formatter(_LOG_FORMAT)

    stderr = logging.StreamHandler()
    stderr.setFormatter(fmt)
    root.addHandler(stderr)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "ragtag_crew.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def _run_telegram_frontend() -> int:
    from ragtag_crew.telegram.bot import build_app

    log = logging.getLogger(__name__)
    backoff = max(1, settings.telegram_restart_backoff_min)
    max_backoff = max(backoff, settings.telegram_restart_backoff_max)

    while True:
        app = build_app()
        started_at = time.monotonic()
        try:
            log.info("Telegram frontend started; polling...")
            app.run_polling(
                drop_pending_updates=True,
                bootstrap_retries=settings.telegram_bootstrap_retries,
            )
            runtime = time.monotonic() - started_at
            log.warning(
                "Telegram frontend stopped unexpectedly after %.1fs; restarting in %ss",
                runtime,
                backoff,
            )
        except KeyboardInterrupt:
            log.info("Telegram frontend interrupted; shutting down.")
            return 0
        except InvalidToken:
            log.exception("Telegram bot token invalid; aborting startup.")
            return 1
        except Exception:
            runtime = time.monotonic() - started_at
            log.exception(
                "Telegram frontend crashed after %.1fs; restarting in %ss",
                runtime,
                backoff,
            )

        time.sleep(backoff)
        if time.monotonic() - started_at >= settings.telegram_health_stale_seconds:
            backoff = max(1, settings.telegram_restart_backoff_min)
        else:
            backoff = min(max_backoff, backoff * 2)


def _check_config() -> None:
    token_set = bool(settings.telegram_bot_token.strip())
    user_count = len(settings.get_allowed_user_ids())

    print("ragtag-crew config check\n")
    print(f"  token       : {'set' if token_set else '<empty>'}")
    print(f"  allowed ids : {user_count} user(s)")
    print(f"  model       : {settings.default_model}")
    print(f"  tools       : {settings.default_tool_preset}")
    print(f"  working_dir : {os.path.abspath(settings.working_dir)}")
    print()

    if not token_set:
        print("FAIL: TELEGRAM_BOT_TOKEN not set")
    else:
        print("OK")


def _show_history_list() -> None:
    from ragtag_crew.session_store import (
        cleanup_expired_sessions,
        list_sessions,
    )

    cleanup_expired_sessions()
    records = list_sessions()
    if not records:
        print("No saved sessions.")
        return

    print("Saved sessions:\n")
    for record in records:
        print(
            f"- chat_id={record.chat_id} model={record.model or '(unknown)'} "
            f"tools={record.tool_preset or '(unknown)'} last_active_at={record.last_active_at:.0f}"
        )


def _show_history(chat_id: int) -> None:
    from ragtag_crew.session_store import (
        cleanup_expired_sessions,
        read_session_payload,
    )

    cleanup_expired_sessions()
    try:
        payload = read_session_payload(chat_id)
    except FileNotFoundError:
        print(f"Session not found: {chat_id}")
        return

    print(f"Session {chat_id}\n")
    print(f"model: {payload.get('model', '(unknown)')}")
    print(f"tools: {payload.get('tool_preset', '(unknown)')}")
    print(f"enabled_skills: {', '.join(payload.get('enabled_skills', [])) or '(none)'}")
    print(f"session_prompt: {payload.get('session_prompt', '') or '(empty)'}")
    print(f"protected_content: {payload.get('protected_content', '') or '(empty)'}")
    print(f"session_summary: {payload.get('session_summary', '') or '(empty)'}")
    print("\nRecent messages:")
    messages = payload.get("messages", []) or []
    if not messages:
        print("- (none)")
        return
    for msg in messages[-10:]:
        role = msg.get("role", "?")
        content = str(msg.get("content", "") or "")
        clipped = " ".join(content.split())
        if len(clipped) > 120:
            clipped = clipped[:117].rstrip() + "..."
        print(f"- {role}: {clipped}")


def _apply_cli_overrides(args: argparse.Namespace) -> None:
    if args.working_dir:
        settings.working_dir = args.working_dir
    if args.model:
        settings.default_model = args.model
    if args.tools:
        settings.default_tool_preset = args.tools
    if args.dev:
        settings.dev_mode = True
    if settings.dev_mode and not args.log_level:
        settings.log_level = "DEBUG"
    if args.log_level:
        settings.log_level = args.log_level


# -- file watcher for --dev mode ----------------------------------------------


def _start_file_watcher() -> None:
    """Watch src/ragtag_crew/**/*.py and restart the process on changes."""
    import threading
    from watchfiles import watch

    src_dir = Path(__file__).resolve().parent
    watch_path = src_dir
    if not watch_path.is_dir():
        return

    def _watch_loop() -> None:
        logging.getLogger(__name__).info("[dev] watching %s for changes...", watch_path)
        try:
            for _changes in watch(watch_path, stop_event=threading.Event()):
                logging.getLogger(__name__).info("[dev] file changed, restarting...")
                os.execv(
                    sys.executable,
                    [sys.executable, "-m", "ragtag_crew.main"] + sys.argv[1:],
                )
        except Exception:
            pass

    t = threading.Thread(target=_watch_loop, daemon=True)
    t.start()


# -- REPL mode ----------------------------------------------------------------


async def _repl_loop() -> None:
    """Terminal interaction mode — talk to the agent without Telegram."""
    from ragtag_crew.agent import AgentSession
    from ragtag_crew.errors import UserAbortedError
    from ragtag_crew.external import ensure_external_capabilities_initialized
    from ragtag_crew.session_store import (
        delete_session as _delete_session,
        load_session as _load_session,
        save_session as _save_session,
    )
    from ragtag_crew.tools import get_tools_for_preset
    from ragtag_crew.trace import TraceCollector

    ensure_external_capabilities_initialized()

    from ragtag_crew.prompts import DEFAULT_SYSTEM_PROMPT
    from ragtag_crew.repl_streamer import ReplStreamer

    _REPL_CHAT_ID = 0

    restored = _load_session(_REPL_CHAT_ID, default_system_prompt=DEFAULT_SYSTEM_PROMPT)
    if restored is not None:
        session = restored
        print(
            f"ragtag-crew REPL  (session restored)  model={session.model}  tools={session.tool_preset}"
        )
    else:
        session = AgentSession(
            model=settings.default_model,
            tools=get_tools_for_preset(settings.default_tool_preset),
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            tool_preset=settings.default_tool_preset,
        )
        print(f"ragtag-crew REPL  model={session.model}  tools={session.tool_preset}")

    streamer = ReplStreamer()
    session.subscribe(streamer.on_event)

    print("输入消息对话，/help 查看命令，Ctrl+C 取消当前回复，/quit 退出\n")

    _REPL_HELP = (
        "REPL 命令：\n"
        "  /new              清空当前会话\n"
        "  /model            查看当前模型\n"
        "  /model <name>     切换模型\n"
        "  /tools            查看当前工具预设\n"
        "  /plan             查看规划模式状态\n"
        "  /plan on|off      开关规划模式\n"
        "  /skills           列出可用技能\n"
        "  /skill use <name> 启用技能\n"
        "  /skill drop <name>禁用技能\n"
        "  /skill clear      清空已启用技能\n"
        "  /cancel           取消当前回复\n"
        "  /help             显示此帮助\n"
        "  /quit             退出 REPL\n"
        "  Ctrl+C            取消当前回复（等效 /cancel）"
    )

    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not text:
            continue

        if text == "/quit":
            break

        if text == "/help":
            print(_REPL_HELP)
            continue

        if text == "/new":
            session.reset()
            _delete_session(_REPL_CHAT_ID)
            print("Session cleared.")
            continue

        if text == "/model":
            print(f"Current: {session.model}")
            continue

        if text.startswith("/model "):
            session.model = text[7:].strip()
            print(f"Switched to: {session.model}")
            continue

        if text == "/cancel":
            session.abort()
            continue

        if text == "/tools":
            print(f"Active tools: {', '.join(t.name for t in session.tools)}")
            continue

        if text == "/plan":
            status = "ON" if session.planning_enabled else "OFF"
            mode = "Plan" if session.planning_enabled else "Build"
            print(f"Current mode: {mode}\nPlanning: {status}")
            continue

        if text.startswith("/plan "):
            action = text[6:].strip().lower()
            if action == "on":
                session.planning_enabled = True
                _save_session(_REPL_CHAT_ID, session)
                print("Plan mode ON — 输出编号计划后再执行。")
            elif action == "off":
                session.planning_enabled = False
                _save_session(_REPL_CHAT_ID, session)
                print("Build mode ON — 直接执行，不输出计划。")
            else:
                print("Usage: /plan on | /plan off")
            continue

        if text == "/skills":
            available = list_skills()
            active = (
                ", ".join(session.enabled_skills)
                if session.enabled_skills
                else "(none)"
            )
            if not available:
                print(
                    f"Active skills: {active}\nNo local skills found in {settings.skills_dir}"
                )
                continue

            print(f"Active skills: {active}\n\nAvailable skills:")
            for skill in available:
                summary = f" - {skill.summary}" if skill.summary else ""
                print(f"- {skill.name}{summary}")
            continue

        if text.startswith("/skill"):
            args = text.split()
            active = (
                ", ".join(session.enabled_skills)
                if session.enabled_skills
                else "(none)"
            )
            if len(args) == 1:
                print(
                    "Usage: /skill use <name> | /skill drop <name> | /skill clear\n"
                    f"Active skills: {active}"
                )
                continue

            action = args[1].lower()
            if action == "clear":
                session.enabled_skills = []
                print("Cleared all active skills.")
                continue

            if len(args) < 3:
                print("Usage: /skill use <name> | /skill drop <name>")
                continue

            skill_name = args[2]
            try:
                skill = get_skill(skill_name)
            except KeyError:
                print(f"Unknown skill: {skill_name}")
                continue

            if action == "use":
                if skill.name not in session.enabled_skills:
                    session.enabled_skills.append(skill.name)
                print(f"Enabled skill: {skill.name}")
                continue

            if action == "drop":
                session.enabled_skills = [
                    name for name in session.enabled_skills if name != skill.name
                ]
                print(f"Disabled skill: {skill.name}")
                continue

            print("Usage: /skill use <name> | /skill drop <name> | /skill clear")
            continue

        if session.is_busy:
            print("(busy, use /cancel to abort)")
            continue

        collector = TraceCollector(chat_id=_REPL_CHAT_ID)
        collector.set_context(
            model=session.model,
            user_input=text,
            tool_preset=session.tool_preset,
            enabled_skills=session.enabled_skills,
            planning_enabled=session.planning_enabled,
        )
        session.subscribe(collector.on_event)

        try:
            result = await session.prompt(text)
            if result and not streamer.buffer.strip():
                print(f"\n{result}\n")
            streamer.buffer = ""
        except UserAbortedError:
            streamer.buffer = ""
        except Exception as exc:
            print(f"\n❌ {exc}\n")
            streamer.buffer = ""
        finally:
            collector.finalize()
            session.unsubscribe(collector.on_event)
            _save_session(_REPL_CHAT_ID, session)

    print("Bye.")


# -- main entry ---------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    _apply_cli_overrides(args)

    if args.check:
        _check_config()
        return 0 if settings.telegram_bot_token.strip() else 1

    if args.history_list:
        _show_history_list()
        return 0

    if args.history is not None:
        _show_history(args.history)
        return 0

    if args.repl:
        _setup_logging()
        asyncio.run(_repl_loop())
        return 0

    _setup_logging()
    log = logging.getLogger(__name__)

    log.info("ragtag_crew starting  pid=%s", os.getpid())

    if not settings.telegram_bot_token:
        log.error(
            "TELEGRAM_BOT_TOKEN not set.  Copy .env.example to .env and fill it in."
        )
        return 1

    log.info("ragtag_crew starting")
    log.info("  model  = %s", settings.default_model)
    log.info("  tools  = %s", settings.default_tool_preset)
    log.info("  cwd    = %s", settings.working_dir)

    from ragtag_crew.tools.bin_resolver import resolve_binary

    try:
        rg_path = resolve_binary("rg")
        log.info("ripgrep ready: %s", rg_path)
    except FileNotFoundError as exc:
        log.error("ripgrep not available: %s", exc)
        log.error("grep/find tools will use Python fallback (slow).")
        log.error("Install ripgrep or ensure internet access for auto-download.")

    if settings.dev_mode:
        _start_file_watcher()

    return _run_telegram_frontend()


if __name__ == "__main__":
    raise SystemExit(main())
