import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from el_ai.telegram.bridge import BridgeJob, TelegramBridgeError
from el_ai.telegram.bridge_worker import BridgeWorkerMetrics, TelegramBridgeWorker


def _job():
    return BridgeJob(
        job_id="c4b7d9a1-8f21-4c62-9a31-2b6d8e0f4a55",
        chat_id=123,
        telegram_user_id=123,
        payload={"command": "status"},
        attempts=1,
        lease_token="a" * 64,
    )


class FakeBridgeClient:
    def __init__(self, jobs=()):
        self.jobs = list(jobs)
        self.completed = []
        self.closed = False
        self.pull_error = None
        self.complete_error = None

    async def pull(self):
        if self.pull_error:
            raise self.pull_error
        return self.jobs.pop(0) if self.jobs else None

    async def complete(self, job, reply):
        if self.complete_error:
            raise self.complete_error
        self.completed.append((job, reply))

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_worker_completes_job_only_after_handler_returns():
    stop = asyncio.Event()
    job = _job()
    client = FakeBridgeClient([job])

    async def handler(received):
        assert received is job
        stop.set()
        return "وضعیت آماده است"

    worker = TelegramBridgeWorker(client, handler, idle_seconds=0.01)
    await worker.run(stop)

    assert client.completed == [(job, "وضعیت آماده است")]
    assert client.closed


@pytest.mark.asyncio
async def test_failed_handler_does_not_acknowledge_job_or_log_exception_text(caplog):
    stop = asyncio.Event()
    job = _job()
    client = FakeBridgeClient([job])

    async def handler(_received):
        stop.set()
        raise RuntimeError("sensitive owner message must not be logged")

    worker = TelegramBridgeWorker(client, handler, idle_seconds=0.01)
    with caplog.at_level(logging.ERROR, logger="el_ai.telegram.bridge_worker"):
        await worker.run(stop)

    assert client.completed == []
    assert "sensitive owner message" not in caplog.text
    assert "handler failed" in caplog.text
    assert client.closed


@pytest.mark.asyncio
async def test_shutdown_cancels_in_flight_handler_without_acknowledging_lease():
    stop = asyncio.Event()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    job = _job()
    client = FakeBridgeClient([job])
    metrics = BridgeWorkerMetrics()

    async def handler(_received):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    worker = TelegramBridgeWorker(
        client,
        handler,
        idle_seconds=0.01,
        metrics=metrics,
    )
    task = asyncio.create_task(worker.run(stop))
    await asyncio.wait_for(started.wait(), timeout=1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert cancelled.is_set()
    assert client.completed == []
    assert client.closed
    assert metrics.jobs_claimed == 1
    assert metrics.jobs_completed == 0
    assert metrics.shutdown_cancellations == 1


@pytest.mark.asyncio
async def test_shutdown_after_pull_skips_handler_and_leaves_lease_unacknowledged():
    stop = asyncio.Event()
    job = _job()
    metrics = BridgeWorkerMetrics()

    class StopAfterPullClient(FakeBridgeClient):
        async def pull(self):
            received = await super().pull()
            stop.set()
            return received

    client = StopAfterPullClient([job])
    handler_calls = []

    async def handler(received):
        handler_calls.append(received)
        return "reply"

    worker = TelegramBridgeWorker(client, handler, idle_seconds=0.01, metrics=metrics)
    await worker.run(stop)

    assert handler_calls == []
    assert client.completed == []
    assert client.closed
    assert metrics.jobs_claimed == 1
    assert metrics.jobs_completed == 0
    assert metrics.shutdown_cancellations == 1


@pytest.mark.asyncio
async def test_shutdown_wins_if_handler_and_stop_signal_finish_together():
    stop = asyncio.Event()
    job = _job()
    client = FakeBridgeClient([job])
    metrics = BridgeWorkerMetrics()

    async def handler(_received):
        stop.set()
        await asyncio.sleep(0.01)
        return "reply"

    worker = TelegramBridgeWorker(client, handler, idle_seconds=0.01, metrics=metrics)
    await worker.run(stop)

    assert client.completed == []
    assert client.closed
    assert metrics.jobs_claimed == 1
    assert metrics.jobs_completed == 0
    assert metrics.shutdown_cancellations == 1


@pytest.mark.asyncio
async def test_shutdown_cancels_blocked_pull_and_closes_client():
    stop = asyncio.Event()
    pull_started = asyncio.Event()
    pull_cancelled = asyncio.Event()

    class BlockingPullClient(FakeBridgeClient):
        async def pull(self):
            pull_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pull_cancelled.set()
                raise

    client = BlockingPullClient()
    worker = TelegramBridgeWorker(client, lambda _job: asyncio.sleep(0), idle_seconds=0.01)
    task = asyncio.create_task(worker.run(stop))
    await asyncio.wait_for(pull_started.wait(), timeout=1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert pull_cancelled.is_set()
    assert client.completed == []
    assert client.closed


@pytest.mark.asyncio
async def test_pull_transport_failure_uses_stop_aware_backoff_and_closes_client():
    stop = asyncio.Event()
    client = FakeBridgeClient()
    client.pull_error = TelegramBridgeError("sanitized", status_code=503)

    async def handler(_job):
        raise AssertionError("handler must not run after failed pull")

    stopper = asyncio.create_task(_stop_after(stop, 0.01))
    worker = TelegramBridgeWorker(
        client,
        handler,
        min_backoff_seconds=0.5,
        max_backoff_seconds=1,
    )
    await worker.run(stop)
    await stopper

    assert client.closed


async def _stop_after(stop, delay):
    await asyncio.sleep(delay)
    stop.set()


@pytest.mark.asyncio
async def test_handler_failure_uses_bounded_exponential_backoff():
    stop = asyncio.Event()
    job = _job()
    client = FakeBridgeClient([job])
    delays = []

    async def handler(_received):
        raise RuntimeError("sanitized handler failure")

    worker = TelegramBridgeWorker(
        client,
        handler,
        min_backoff_seconds=0.25,
        max_backoff_seconds=1,
    )

    async def capture_wait(_stop, delay):
        delays.append(delay)
        stop.set()

    worker._wait_or_stop = capture_wait
    await worker.run(stop)

    assert delays == [0.25]
    assert client.completed == []
    assert client.closed


@pytest.mark.asyncio
async def test_completion_transport_failure_uses_exponential_backoff():
    stop = asyncio.Event()
    job = _job()
    client = FakeBridgeClient([job])
    client.complete_error = TelegramBridgeError("sanitized", status_code=503)
    delays = []

    async def handler(_received):
        return "reply"

    worker = TelegramBridgeWorker(
        client,
        handler,
        min_backoff_seconds=0.25,
        max_backoff_seconds=1,
    )

    async def capture_wait(_stop, delay):
        delays.append(delay)
        stop.set()

    worker._wait_or_stop = capture_wait
    await worker.run(stop)

    assert delays == [0.25]
    assert client.completed == []
    assert client.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"idle_seconds": 0},
        {"min_backoff_seconds": 0},
        {"min_backoff_seconds": 2, "max_backoff_seconds": 1},
    ],
)
async def test_worker_rejects_invalid_timing_bounds(kwargs):
    with pytest.raises(ValueError):
        TelegramBridgeWorker(FakeBridgeClient(), lambda _job: None, **kwargs)


def test_bridge_worker_metrics_are_aggregate_only_and_emit_structured_json(caplog):
    now = datetime.now(UTC)
    metrics = BridgeWorkerMetrics(started_at=now - timedelta(seconds=12))
    metrics.record_pull(True)
    metrics.record_pull(False)
    metrics.record_completion()
    metrics.record_handler_failure()
    metrics.record_transport_failure()
    metrics.record_shutdown_cancellation()

    snapshot = metrics.snapshot(now=now)
    assert snapshot == {
        "event": "telegram_bridge_worker_metrics",
        "uptime_seconds": 12,
        "polls": 2,
        "empty_polls": 1,
        "jobs_claimed": 1,
        "jobs_completed": 1,
        "handler_failures": 1,
        "transport_failures": 1,
        "shutdown_cancellations": 1,
        "last_successful_pull_age_seconds": 0,
    }
    assert not {"job_id", "chat_id", "telegram_user_id", "payload", "lease_token", "reply"} & snapshot.keys()

    with caplog.at_level(logging.INFO, logger="el_ai.telegram.bridge_worker"):
        metrics.emit(logging.getLogger("el_ai.telegram.bridge_worker"))
    emitted = json.loads(caplog.records[-1].message)
    assert emitted == snapshot
    assert "123" not in caplog.text
    assert "lease_token" not in caplog.text
