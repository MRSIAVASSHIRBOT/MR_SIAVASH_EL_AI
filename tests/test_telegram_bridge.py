import hashlib
import hmac
import json
import re
import time
from uuid import uuid4

import httpx
import pytest

from el_ai.telegram.bridge import (
    BridgeJob,
    TelegramBridgeClient,
    TelegramBridgeError,
    _MAX_RESPONSE_BYTES,
)


SECRET = "bridge-shared-secret-material-at-least-32"
LEASE = "a" * 64


def _job_payload():
    return {
        "job_id": str(uuid4()),
        "chat_id": 123456,
        "telegram_user_id": 123456,
        "payload": {"command": "status"},
        "attempts": 1,
        "lease_token": LEASE,
    }


def _assert_signature(request: httpx.Request) -> None:
    body = request.read()
    timestamp = request.headers["X-Bridge-Timestamp"]
    nonce = request.headers["X-Bridge-Nonce"]
    supplied = request.headers["X-Bridge-Signature"]
    assert re.fullmatch(r"\d{10}", timestamp)
    assert abs(int(time.time()) - int(timestamp)) <= 60
    assert re.fullmatch(r"[A-Za-z0-9_-]{20,80}", nonce)
    message = "\n".join(
        (
            timestamp,
            nonce,
            request.method,
            request.url.path,
            hashlib.sha256(body).hexdigest(),
        )
    ).encode()
    expected = hmac.new(SECRET.encode(), message, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(supplied, expected)


@pytest.mark.asyncio
async def test_pull_signs_exact_body_and_parses_leased_job():
    job = _job_payload()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        _assert_signature(request)
        assert json.loads(request.read()) == {}
        return httpx.Response(200, json={"job": job})

    client = TelegramBridgeClient(
        "https://bridge.example.test",
        SECRET,
        transport=httpx.MockTransport(handler),
    )
    result = await client.pull()

    assert isinstance(result, BridgeJob)
    assert result.job_id == job["job_id"]
    assert result.payload == {"command": "status"}
    assert result.lease_token == LEASE
    assert calls[0].url.path == "/bridge/pull"
    await client.close()


@pytest.mark.asyncio
async def test_pull_returns_none_for_empty_queue():
    client = TelegramBridgeClient(
        "https://bridge.example.test",
        SECRET,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"job": None})
        ),
    )
    assert await client.pull() is None
    await client.close()


@pytest.mark.asyncio
async def test_complete_signs_lease_bound_reply():
    job_data = _job_payload()
    job = TelegramBridgeClient._parse_job(job_data)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        _assert_signature(request)
        calls.append(json.loads(request.read()))
        return httpx.Response(200, json={"ok": True})

    client = TelegramBridgeClient(
        "https://bridge.example.test",
        SECRET,
        transport=httpx.MockTransport(handler),
    )
    await client.complete(job, "پاسخ امن ✨")

    assert calls == [{
        "job_id": job.job_id,
        "lease_token": LEASE,
        "reply": "پاسخ امن ✨",
    }]
    await client.close()


@pytest.mark.asyncio
async def test_expired_lease_is_reported_without_response_body():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "lease_not_valid", "secret": "do-not-leak"})

    client = TelegramBridgeClient(
        "https://bridge.example.test",
        SECRET,
        transport=httpx.MockTransport(handler),
    )
    job = TelegramBridgeClient._parse_job(_job_payload())
    with pytest.raises(TelegramBridgeError) as error:
        await client.complete(job, "reply")
    assert error.value.status_code == 409
    assert "do-not-leak" not in str(error.value)
    assert error.value.__suppress_context__ is True
    await client.close()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(chat_id=True),
        lambda value: value.update(telegram_user_id=-1),
        lambda value: value.update(attempts=0),
        lambda value: value.update(lease_token="short"),
        lambda value: value.update(payload=[]),
        lambda value: value.update(job_id="not-a-uuid"),
    ],
)
def test_job_parser_rejects_invalid_lease_envelopes(mutate):
    value = _job_payload()
    mutate(value)
    with pytest.raises(TelegramBridgeError, match="invalid job"):
        TelegramBridgeClient._parse_job(value)


@pytest.mark.asyncio
async def test_pull_rejects_unexpected_response_shape():
    client = TelegramBridgeClient(
        "https://bridge.example.test",
        SECRET,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"job": None, "extra": True})
        ),
    )
    with pytest.raises(TelegramBridgeError, match="invalid pull response"):
        await client.pull()
    await client.close()


def test_bridge_client_requires_https_and_a_strong_shared_secret():
    with pytest.raises(ValueError, match="HTTPS"):
        TelegramBridgeClient("http://bridge.example.test", SECRET)
    with pytest.raises(ValueError, match="32 characters"):
        TelegramBridgeClient("https://bridge.example.test", "short")



@pytest.mark.asyncio
async def test_pull_rejects_non_json_success_response_without_exposing_body():
    client = TelegramBridgeClient(
        "https://bridge.example.test",
        SECRET,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                text="private upstream response",
                headers={"content-type": "text/html; charset=utf-8"},
            )
        ),
    )
    with pytest.raises(TelegramBridgeError, match="invalid response") as error:
        await client.pull()
    assert "private upstream response" not in str(error.value)
    await client.close()


@pytest.mark.asyncio
async def test_pull_rejects_declared_oversized_response_before_json_parsing():
    client = TelegramBridgeClient(
        "https://bridge.example.test",
        SECRET,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=b'{"job":null}',
                headers={
                    "content-type": "application/json",
                    "content-length": str(_MAX_RESPONSE_BYTES + 1),
                },
            )
        ),
    )
    with pytest.raises(TelegramBridgeError, match="exceeded the allowed size"):
        await client.pull()
    await client.close()


@pytest.mark.asyncio
async def test_pull_rejects_oversized_chunked_response_without_content_length():
    class OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b" " * (_MAX_RESPONSE_BYTES + 1)

        async def aclose(self):
            return None

    client = TelegramBridgeClient(
        "https://bridge.example.test",
        SECRET,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                stream=OversizedStream(),
                headers={"content-type": "application/json"},
            )
        ),
    )
    with pytest.raises(TelegramBridgeError, match="exceeded the allowed size"):
        await client.pull()
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b'{"job":null,"job":null}',
        b'{"job":NaN}',
        b'{"job":Infinity}',
    ],
)
async def test_pull_rejects_ambiguous_or_nonstandard_json(body):
    client = TelegramBridgeClient(
        "https://bridge.example.test",
        SECRET,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=body,
                headers={"content-type": "application/json"},
            )
        ),
    )
    with pytest.raises(TelegramBridgeError, match="invalid response"):
        await client.pull()
    await client.close()
