"""Long polling loop with bounded retry backoff and in-memory update offsets."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from el_ai.application import TelegramApplication
from el_ai.telegram import TelegramAPIError, TelegramClient

logger = logging.getLogger(__name__)


async def process_updates(
    application: TelegramApplication,
    updates: Sequence[dict[str, Any]],
    *,
    offset: int | None,
) -> int | None:
    next_offset = offset
    for update in updates:
        update_id = update.get("update_id")
        if not isinstance(update_id, int) or isinstance(update_id, bool) or update_id < 0:
            continue
        try:
            await application.handle_update(update)
        except Exception:
            logger.exception("Telegram update handler failed (update_id=%s).", update_id)
        next_offset = max(next_offset or 0, update_id + 1)
    return next_offset


async def run_polling(
    application: TelegramApplication,
    client: TelegramClient,
    *,
    poll_timeout_seconds: int,
    stop_event: asyncio.Event | None = None,
) -> None:
    offset: int | None = None
    backoff_seconds = 1
    while stop_event is None or not stop_event.is_set():
        try:
            updates = await client.get_updates(offset=offset, timeout=poll_timeout_seconds)
            backoff_seconds = 1
        except TelegramAPIError as exc:
            logger.warning("Telegram polling request failed: %s", exc)
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, 30)
            continue
        offset = await process_updates(application, updates, offset=offset)
