import asyncio

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

    async def pull(self):
        if self.pull_error:
            raise self.pull_error
        return self.jobs.pop(0) if self.jobs else None

    async def complete(self, job, reply):
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
async def test_failed_handler_does_not_acknowledge_job_and_worker_stops_cleanly():
    stop = asyncio.Event()
    job = _job()
    client = FakeBridgeClient([job])

    async def handler(_received):
        stop.set()
        raise RuntimeError("sensitive payload must not be logged")

    worker = TelegramBridgeWorker(client, handler, idle_seconds=0.01)
    await worker.run(stop)

    assert client.completed == []
    assert client.closed


@pytest.mark.asyncio
async def test_transport_failure_uses_stop_aware_backoff_and_closes_client():
    stop = asyncio.Event()
    client = FakeBridgeClient()
    client.pull_error = TelegramBridgeError("sanitized", status_code=503)

    async def handler(_job):
        raise AssertionError("handler must not run after failed pull")

    async def stop_soon():
        await asyncio.sleep(0.01)
        stop.set()

    stopper = asyncio.create_task(stop_soon())
    worker = TelegramBridgeWorker(
        client,
        handler,
        min_backoff_seconds=0.5,
        max_backoff_seconds=1,
    )
    await worker.run(stop)
    await stopper

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
