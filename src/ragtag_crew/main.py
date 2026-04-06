"""Entry point for ragtag_crew."""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from ragtag_crew.config import settings

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
        logging.getLogger(__name__).info(
            "[dev] watching %s for changes...", watch_path
        )
        try:
            for _changes in watch(watch_path, stop_event=threading.Event()):
                logging.getLogger(__name__).info("[dev] file changed, restarting...")
                os.execv(sys.executable, [sys.executable] + sys.argv)
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
    from ragtag_crew.tools import get_tools_for_preset

    ensure_external_capabilities_initialized()

    session = AgentSession(
        model=settings.default_model,
        tools=get_tools_for_preset(settings.default_tool_preset),
        system_prompt="You are a concise coding assistant. Be brief.",
        tool_preset=settings.default_tool_preset,
    )

    def _on_event(event_type: str, **kwargs) -> None:
        if event_type == "tool_execution_start":
            tc = kwargs["tool_call"]
            args_str = ", ".join(f"{k}={v}" for k, v in tc.arguments.items())
            print(f"\n⏳ {tc.name}({args_str})")
        elif event_type == "tool_execution_end":
            pass
        elif event_type == "cancelled":
            print("\n⚠️ 已取消")
        elif event_type == "error":
            print(f"\n❌ {kwargs.get('error', 'Unknown error')}")

    loop = asyncio.get_running_loop()
    session.subscribe(lambda *a, **kw: loop.create_task(_on_event(*a, **kw)))

    print(f"ragtag-crew REPL  model={session.model}  tools={session.tool_preset}")
    print("输入消息对话，Ctrl+C 取消当前回复，/quit 退出\n")

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

        if text == "/new":
            session.reset()
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

        if session.is_busy:
            print("(busy, use /cancel to abort)")
            continue

        try:
            result = await session.prompt(text)
            if result:
                print(f"\n{result}\n")
        except UserAbortedError:
            pass
        except Exception as exc:
            print(f"\n❌ {exc}\n")

    print("Bye.")


# -- main entry ---------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    _apply_cli_overrides(args)

    if args.check:
        _check_config()
        return 0 if settings.telegram_bot_token.strip() else 1

    if args.repl:
        _setup_logging()
        asyncio.run(_repl_loop())
        return 0

    _setup_logging()
    log = logging.getLogger(__name__)

    log.info("ragtag_crew starting  pid=%s", os.getpid())

    if not settings.telegram_bot_token:
        log.error("TELEGRAM_BOT_TOKEN not set.  Copy .env.example to .env and fill it in.")
        return 1

    log.info("ragtag_crew starting")
    log.info("  model  = %s", settings.default_model)
    log.info("  tools  = %s", settings.default_tool_preset)
    log.info("  cwd    = %s", settings.working_dir)

    from ragtag_crew.telegram.bot import build_app
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

    app = build_app()
    log.info("Telegram frontend started; polling...")
    app.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
