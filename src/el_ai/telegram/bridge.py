"""Authenticated client for the Cloudflare Telegram edge job bridge."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import httpx


_MAX_REPLY_LENGTH = 4000
_MAX_RESPONSE_BYTES = 64 * 1024
_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,80}$")
_LEASE_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class TelegramBridgeError(RuntimeError):
    """Sanitized bridge transport or protocol failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class BridgeJob:
    """One leased owner job returned by the edge queue."""

    job_id: str
    chat_id: int
    telegram_user_id: int
    payload: dict[str, object]
    attempts: int
    lease_token: str


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _serialize_payload(payload: dict[str, object]) -> bytes:
    if not isinstance(payload, dict):
        raise TypeError("Bridge payload must be an object")
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("Bridge payload is not valid JSON") from exc





def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate keys at every level instead of silently keeping the last value."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    """Reject JavaScript-only NaN/Infinity tokens that are not valid JSON."""
    raise ValueError("Non-standard JSON constant")


class TelegramBridgeClient:
    """Sign and send the exact request format accepted by the edge Worker.

    Signature input: timestamp, nonce, HTTP method, URL path, and SHA-256
    digest of the exact UTF-8 request body, separated by newline characters.
    """

    def __init__(
        self,
        base_url: str,
        shared_secret: str,
        *,
        timeout_seconds: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        is_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (parsed.scheme != "https" and not is_loopback)
        ):
            raise ValueError("Bridge URL must be HTTPS; HTTP is allowed only for loopback tests")
        if not isinstance(shared_secret, str) or len(shared_secret) < 32:
            raise ValueError("Bridge shared secret must contain at least 32 characters")
        if timeout_seconds <= 0:
            raise ValueError("Bridge timeout must be positive")

        self._secret = shared_secret.encode("utf-8")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            transport=transport,
        )

    def _headers(self, method: str, path: str, body: bytes) -> dict[str, str]:
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        body_hash = hashlib.sha256(body).hexdigest()
        signed_message = "\n".join(
            (timestamp, nonce, method, path, body_hash)
        ).encode("utf-8")
        signature = hmac.new(self._secret, signed_message, hashlib.sha256).hexdigest()
        return {
            "content-type": "application/json",
            "X-Bridge-Timestamp": timestamp,
            "X-Bridge-Nonce": nonce,
            "X-Bridge-Signature": signature,
        }

    @staticmethod
    async def _decode_response(response: httpx.Response) -> dict[str, object]:
        """Read a strict JSON response under a hard decoded-byte limit."""
        media_type = (
            response.headers.get("content-type", "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        if media_type != "application/json":
            raise TelegramBridgeError("Telegram bridge returned an invalid response")

        content_length = response.headers.get("content-length")
        if content_length is not None and (
            not content_length.isascii()
            or not content_length.isdecimal()
            or int(content_length) > _MAX_RESPONSE_BYTES
        ):
            raise TelegramBridgeError("Telegram bridge response exceeded the allowed size")

        chunks: list[bytes] = []
        total_bytes = 0
        try:
            async for chunk in response.aiter_bytes():
                total_bytes += len(chunk)
                if total_bytes > _MAX_RESPONSE_BYTES:
                    raise TelegramBridgeError(
                        "Telegram bridge response exceeded the allowed size"
                    )
                chunks.append(chunk)
            result = json.loads(
                b"".join(chunks).decode("utf-8"),
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        except TelegramBridgeError:
            raise
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise TelegramBridgeError("Telegram bridge returned an invalid response") from None
        if not isinstance(result, dict):
            raise TelegramBridgeError("Telegram bridge returned an invalid response")
        return result

    async def _post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        body = _serialize_payload(payload)
        try:
            async with self._client.stream(
                "POST",
                path,
                content=body,
                headers=self._headers("POST", "/" + path.lstrip("/"), body),
            ) as response:
                response.raise_for_status()
                result = await self._decode_response(response)
        except httpx.HTTPStatusError as exc:
            raise TelegramBridgeError(
                "Telegram bridge rejected the request",
                status_code=exc.response.status_code,
            ) from None
        except httpx.HTTPError:
            raise TelegramBridgeError("Telegram bridge request failed") from None
        if "error" in result:
            raise TelegramBridgeError("Telegram bridge returned an error response")
        return result

    @staticmethod
    def _parse_job(value: object) -> BridgeJob | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TelegramBridgeError("Telegram bridge returned an invalid job")
        job_id = value.get("job_id")
        try:
            normalized_job_id = str(UUID(job_id)) if isinstance(job_id, str) else ""
        except ValueError:
            normalized_job_id = ""
        chat_id = value.get("chat_id")
        user_id = value.get("telegram_user_id")
        payload = value.get("payload")
        attempts = value.get("attempts")
        lease_token = value.get("lease_token")
        if (
            not normalized_job_id
            or not _positive_int(chat_id)
            or not _positive_int(user_id)
            or not isinstance(payload, dict)
            or not _positive_int(attempts)
            or not isinstance(lease_token, str)
            or not _LEASE_PATTERN.fullmatch(lease_token)
        ):
            raise TelegramBridgeError("Telegram bridge returned an invalid job")
        return BridgeJob(
            job_id=normalized_job_id,
            chat_id=chat_id,
            telegram_user_id=user_id,
            payload=payload,
            attempts=attempts,
            lease_token=lease_token,
        )

    async def pull(self) -> BridgeJob | None:
        """Claim one job; return None when the durable queue is empty."""
        result = await self._post("/bridge/pull", {})
        if set(result) != {"job"}:
            raise TelegramBridgeError("Telegram bridge returned an invalid pull response")
        return self._parse_job(result["job"])

    async def complete(self, job: BridgeJob, reply: str) -> None:
        """Deliver a reply only while the exact job lease remains valid."""
        if not isinstance(job, BridgeJob):
            raise TypeError("A BridgeJob is required")
        if not isinstance(reply, str) or not reply.strip() or len(reply) > _MAX_REPLY_LENGTH:
            raise ValueError("Bridge reply must contain 1 to 4000 characters")
        await self._post(
            "/bridge/complete",
            {
                "job_id": job.job_id,
                "lease_token": job.lease_token,
                "reply": reply,
            },
        )

    async def close(self) -> None:
        await self._client.aclose()
