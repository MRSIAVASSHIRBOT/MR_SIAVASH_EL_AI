"""Command-line entry point for local long-polling development."""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

from el_ai.application import TelegramApplication
from el_ai.config import ConfigurationError, Settings
from el_ai.polling import run_polling
from el_ai.telegram import TelegramClient


def main() -> None:
    load_dotenv()
    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from None

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    async def run() -> None:
        async with TelegramClient(
            settings.telegram_bot_token,
            timeout=settings.request_timeout_seconds,
        ) as client:
            application = TelegramApplication(
                client,
                owner_ids=settings.owner_ids,
                public_enabled=settings.public_enabled,
            )
            await run_polling(
                application,
                client,
                poll_timeout_seconds=settings.poll_timeout_seconds,
            )

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Telegram polling stopped.")
