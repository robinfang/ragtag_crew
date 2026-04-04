"""Entry point for ragtag_crew."""

import argparse
import logging
from collections.abc import Sequence

from ragtag_crew.config import settings


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


def main(argv: Sequence[str] | None = None) -> int:
    _parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    )
    log = logging.getLogger(__name__)

    # Validate essential config
    if not settings.telegram_bot_token:
        log.error("TELEGRAM_BOT_TOKEN not set.  Copy .env.example to .env and fill it in.")
        return 1

    log.info("ragtag_crew starting")
    log.info("  model  = %s", settings.default_model)
    log.info("  tools  = %s", settings.default_tool_preset)
    log.info("  cwd    = %s", settings.working_dir)

    from ragtag_crew.telegram.bot import build_app

    app = build_app()
    log.info("Telegram frontend started; polling...")
    app.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
