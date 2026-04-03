"""Entry point."""

import asyncio

from src.config import settings


def main():
    """Start the Telegram bot."""
    # TODO: implement
    # 1. validate settings (token, allowed_user_ids)
    # 2. build Application
    # 3. run polling
    print(f"py-telegram-agent starting with model={settings.default_model}")


if __name__ == "__main__":
    main()
