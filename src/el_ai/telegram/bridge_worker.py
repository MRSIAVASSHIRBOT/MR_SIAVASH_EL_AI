"""Bounded polling worker for the authenticated Cloudflare Telegram bridge."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from el_ai.telegram.bridge import BridgeJob, TelegramBridgeClient, TelegramBridgeError

LOGGER = logging.getLogger("el_ai.telegram.bridge_worker")

JobHandler = Callable[[BridgeJob], Awaitable[str]]
_STOPPED = object()


@dataclass(slots=True)
class BridgeWorkerMetrics:
    """Process-local aggregate metrics; never retain job or owner identifiers."""

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    polls: int = 0
    empty_polls: int = 0
    jobs_claimed: int = 0
    jobs_completed: int = 0
    handler_failures: int = 0
    transport_failures: int = 0
    shutdown_cancellations: int = 0
    last_successful_pull_at: datetime | None = None

    def record_pull(self, job_available: bool) -> None:
        self.polls += 1
        self.last_successful_pull_at = datetime.now(UTC)
        if job_available:
            self.jobs_claimed += 1
        else:
            self.empty_polls += 1

    def record_completion(self) -> None:
        self.jobs_completed += 1

    def record_handler_failure(self) -> None:
        self.handler_failures += 1

    def record_transport_failure(self) -> None:
        self.transport_failures += 1

    def record_shutdown_cancellation(self) -> None:
        self.shutdown_cancellations += 1

    def snapshot(self, *, now: datetime | None = None) -> dict[str, object]:
        current = now or datetime.now(UTC)
        last_pull_age = (
            max(0, int((current - self.last_successful_pull_at).total_seconds()))
            if self.last_successful_pull_at is not None
            else None
        )
        return {
            "event": "telegram_bridge_worker_metrics",
            "uptime_seconds": max(0, int((current - self.started_at).total_seconds())),
            "polls": self.polls,
            "empty_polls": self.empty_polls,
            "jobs_claimed": self.jobs_claimed,
            "jobs_completed": self.jobs_completed,
            "handler_failures": self.handler_failures,
            "transport_failures": self.transport_failures,
            "shutdown_cancellations": self.shutdown_cancellations,
            "last_successful_pull_age_seconds": last_pull_age,
        }

    def emit(self, logger: logging.Logger) -> dict[str, object]:
        """Emit aggregate operational state without request or identity data."""
        payload = self.snapshot()
        logger.info(
            "%s",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        return payload


class TelegramBridgeWorker:
    """Poll leased jobs and complete them only after application handling succeeds.

    Failed handlers deliberately do not acknowledge a job. The edge lease expires
    and makes it eligible for a later attempt; no local unbounded retry queue is
    created and no job payload or reply is written to logs. Shutdown cancels an
    in-flight handler without acknowledging its lease, allowing safe redelivery.
    """

    def __init__(
        self,
        client: TelegramBridgeClient,
        handler: JobHandler,
        *,
        idle_seconds: float = 1.0,
        min_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
        metrics: BridgeWorkerMetrics | None = None,
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
        self._metrics = metrics or BridgeWorkerMetrics()

    async def _wait_or_stop(self, stop: asyncio.Event, delay: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            return

    async def _pull_or_stop(self, stop: asyncio.Event) -> BridgeJob | None | object:
        """Cancel a blocked bridge pull promptly when shutdown is requested."""
        pull_task = asyncio.create_task(self._client.pull())
        stop_task = asyncio.create_task(stop.wait())
        try:
            done, _pending = await asyncio.wait(
                (pull_task, stop_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            # If pull completed, return it; run() checks stop before handling a job.
            if pull_task in done:
                return await pull_task
            return _STOPPED
        finally:
            for task in (pull_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(pull_task, stop_task, return_exceptions=True)

    async def _handle_or_stop(self, stop: asyncio.Event, job: BridgeJob) -> str | object:
        """Cancel application work on shutdown and leave the edge lease unacknowledged."""
        handler_task = asyncio.create_task(self._handler(job))
        stop_task = asyncio.create_task(stop.wait())
        try:
            done, _pending = await asyncio.wait(
                (handler_task, stop_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Shutdown takes priority when both tasks become ready in the same
            # event-loop turn. Leave the lease unacknowledged for redelivery.
            if stop_task in done:
                return _STOPPED
            if handler_task in done:
                return await handler_task
            return _STOPPED
        finally:
            for task in (handler_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(handler_task, stop_task, return_exceptions=True)

    async def run(self, stop: asyncio.Event) -> None:
        """Run until stop is set; always emit aggregate metrics and close the client."""
        backoff = self._min_backoff
        try:
            while not stop.is_set():
                try:
                    job = await self._pull_or_stop(stop)
                    if job is _STOPPED:
                        break
                    self._metrics.record_pull(job is not None)
                    # A shutdown requested while pull was in flight must not start
                    # application work or acknowledge the lease.
                    if stop.is_set():
                        if job is not None:
                            self._metrics.record_shutdown_cancellation()
                        break
                    if job is None:
                        backoff = self._min_backoff
                        await self._wait_or_stop(stop, self._idle_seconds)
                        continue

                    try:
                        reply = await self._handle_or_stop(stop, job)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - do not leak exception text or kill worker
                        self._metrics.record_handler_failure()
                        LOGGER.error(
                            "Telegram bridge job handler failed; lease will expire and permit retry"
                        )
                        await self._wait_or_stop(stop, self._idle_seconds)
                        continue

                    if reply is _STOPPED:
                        self._metrics.record_shutdown_cancellation()
                        break

                    # Keep completion outside the handler exception boundary:
                    # transport failures must use the same exponential backoff as pull.
                    await self._client.complete(job, reply)
                    self._metrics.record_completion()
                    backoff = self._min_backoff
                except asyncio.CancelledError:
                    raise
                except TelegramBridgeError as exc:
                    self._metrics.record_transport_failure()
                    LOGGER.warning(
                        "Telegram bridge transport failed; status=%s",
                        exc.status_code,
                    )
                    await self._wait_or_stop(stop, backoff)
                    backoff = min(backoff * 2, self._max_backoff)
        finally:
            try:
                self._metrics.emit(LOGGER)
            finally:
                await self._client.close()
