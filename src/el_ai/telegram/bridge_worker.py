"""Bounded polling worker for the authenticated Cloudflare Telegram bridge."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from el_ai.telegram.bridge import BridgeJob, TelegramBridgeClient, TelegramBridgeError

LOGGER = logging.getLogger("el_ai.telegram.bridge_worker")

JobHandler = Callable[[BridgeJob], Awaitable[str]]


class TelegramBridgeWorker:
    """Poll leased jobs and complete them only after application handling succeeds.

    Failed handlers deliberately do not acknowledge a job. The edge lease expires
    and makes it eligible for a later attempt; no local unbounded retry queue is
    created and no job payload or reply is written to logs.
    """

    def __init__(
        self,
        client: TelegramBridgeClient,
        handler: JobHandler,
        *,
        idle_seconds: float = 1.0,
        min_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
    ) -> None:
        if idle_seconds <= 0:
            raise ValueError("Bridge worker idle interval must be positive")
        if min_backoff_seconds <= 0 or max_backoff_seconds < min_backoff_seconds:
            raise ValueError("Bridge worker backoff bounds are invalid")
        self._client = client
        self._handler = handler
        self._idle_seconds = idle_seconds
        self._min_backoff = min_backoff_seconds
        self._max_backoff = max_backoff_seconds

    async def _wait_or_stop(self, stop: asyncio.Event, delay: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            return

    async def run(self, stop: asyncio.Event) -> None:
        """Run until stop is set; always close the underlying HTTP client."""
        backoff = self._min_backoff
        try:
            while not stop.is_set():
                try:
                    job = await self._client.pull()
                    backoff = self._min_backoff
                    if job is None:
                        await self._wait_or_stop(stop, self._idle_seconds)
                        continue
                    try:
                        reply = await self._handler(job)
                        await self._client.complete(job, reply)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - one failed job must not kill the worker
                        LOGGER.exception(
                            "Telegram bridge job failed; lease will expire and permit retry"
                        )
                        await self._wait_or_stop(stop, self._idle_seconds)
                except asyncio.CancelledError:
                    raise
                except TelegramBridgeError as exc:
                    LOGGER.warning(
                        "Telegram bridge transport failed; status=%s",
                        exc.status_code,
                    )
                    await self._wait_or_stop(stop, backoff)
                    backoff = min(backoff * 2, self._max_backoff)
        finally:
            await self._client.close()
