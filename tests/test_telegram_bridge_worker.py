import asyncio
import logging

import pytest

from el_ai.telegram.bridge import BridgeJob, TelegramBridgeError
from el_ai.telegram.bridge_worker import TelegramBridgeWorker


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
