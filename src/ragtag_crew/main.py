"""Entry point for ragtag_crew."""

import logging
import sys

from ragtag_crew.config import settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    )
    log = logging.getLogger(__name__)

    # Validate essential config
    if not settings.telegram_bot_token:
        log.error("TELEGRAM_BOT_TOKEN not set.  Copy .env.example to .env and fill it in.")
        sys.exit(1)

    log.info("ragtag_crew starting")
    log.info("  model  = %s", settings.default_model)
    log.info("  tools  = %s", settings.default_tool_preset)
    log.info("  cwd    = %s", settings.working_dir)

    from ragtag_crew.telegram.bot import build_app

    app = build_app()
    log.info("Telegram frontend started; polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
