"""Entry point for ragtag_crew."""

import argparse
import logging
import logging.handlers
import os
from collections.abc import Sequence
from pathlib import Path

from ragtag_crew.config import settings

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"


def build_arg_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="ragtag-crew",
        description="Self-hosted Telegram AI coding agent with Telegram, local tools, external capabilities, and browser automation.",
        epilog=(
            "Examples:\n"
            "  ragtag-crew\n"
            "  uv run python -m ragtag_crew.main -h\n\n"
            "Notes:\n"
            "  - Normal startup requires TELEGRAM_BOT_TOKEN and ALLOWED_USER_IDS."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


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


def main(argv: Sequence[str] | None = None) -> int:
    _parse_args(argv)

    _setup_logging()
    log = logging.getLogger(__name__)

    log.info("ragtag_crew starting  pid=%s", os.getpid())

    # Validate essential config
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
