"""Entry point for ragtag_crew."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
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
    "  ragtag-crew --check             检查配置是否完整\n"
    "  ragtag-crew --working-dir /tmp  指定工作目录\n"
    "  ragtag-crew --model claude-3-5-sonnet-20241022 --tools readonly"
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
    if args.log_level:
        settings.log_level = args.log_level


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    _apply_cli_overrides(args)

    if args.check:
        _check_config()
        return 0 if settings.telegram_bot_token.strip() else 1

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

    app = build_app()
    log.info("Telegram frontend started; polling...")
    app.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
