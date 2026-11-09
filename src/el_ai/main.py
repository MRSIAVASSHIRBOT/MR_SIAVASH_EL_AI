"""Long-polling runtime for local development and controlled first release."""

from __future__ import annotations

import asyncio
import logging
import signal

from el_ai.config import ConfigurationError, Settings
from el_ai.telegram.access import AccessController
from el_ai.telegram.api import TelegramAPIError, TelegramBotAPI
from el_ai.telegram.router import MessageRouter

LOGGER = logging.getLogger("el_ai")


async def run(settings: Settings) -> None:
    api = TelegramBotAPI(
        settings.telegram_bot_token,
        timeout_seconds=settings.request_timeout_seconds,
    )
    access = AccessController(
        settings.owner_ids,
        public_enabled=settings.public_enabled,
        rate_limit=settings.rate_limit_per_minute,
    )
    router = MessageRouter(access)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    offset: int | None = None
    backoff = 1
    try:
        LOGGER.info("Telegram polling started; environment=%s", settings.environment)
        while not stop.is_set():
            try:
                updates = await api.get_updates(offset=offset, timeout=settings.poll_timeout_seconds)
                backoff = 1
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = max(offset or 0, update_id + 1)
                    message = update.get("message")
                    if not isinstance(message, dict):
                        continue
                    chat = message.get("chat") or {}
                    chat_id = chat.get("id")
                    if not isinstance(chat_id, int):
                        continue
                    response = router.route(message)
                    if response is None:
                        continue
                    try:
                        await api.send_message(chat_id, response[:4096])
                    except TelegramAPIError:
                        # Do not blindly retry a send with an ambiguous provider outcome.
                        LOGGER.warning("Telegram reply failed or has unknown outcome; update=%s", update_id)
            except TelegramAPIError:
                LOGGER.warning("Telegram polling request failed; retrying with bounded backoff")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 30)
    finally:
        await api.close()
        LOGGER.info("Telegram polling stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
