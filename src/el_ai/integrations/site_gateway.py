"""Read-only client for the public MR_SIAVASH_AI assistant Gateway."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx


class SiteGatewayError(RuntimeError):
    """Sanitized failure from the public website Gateway."""


@dataclass(frozen=True, slots=True)
class PublicSource:
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class PublicAnswer:
    answer: str
    mode: str
    handoff: bool
    sources: tuple[PublicSource, ...]


class SiteGatewayClient:
    """Calls only the public assistant endpoint; never carries private Hub context."""

    MAX_MESSAGE_LENGTH = 1200
    MAX_RESPONSE_LENGTH = 3000
    MAX_RESPONSE_BODY_BYTES = 32_768
    MAX_SOURCES = 3
    MAX_REQUESTS_PER_MINUTE = 15

    def __init__(
        self,
        api_base_url: str,
        website_base_url: str,
        *,
        timeout_seconds: float = 12.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_origin = self._validate_base_url(api_base_url)
        self._website_base = self._validate_base_url(website_base_url)
        self._client = httpx.AsyncClient(
            base_url=self._api_origin + "/",
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            transport=transport,
            headers={"Accept": "application/json"},
        )
        self._request_times: list[float] = []

    @staticmethod
    def _validate_base_url(value: str) -> str:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Public site URL must be a clean HTTP(S) base URL")
        if parsed.scheme != "https" and parsed.hostname not in {
            "127.0.0.1", "localhost", "::1"
        }:
            raise ValueError("Remote public site URLs must use HTTPS")
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("Public site URL contains an invalid port") from exc
        return value.strip().rstrip("/")

    def _source_url(self, href: object) -> str | None:
        if not isinstance(href, str) or not href or len(href) > 500:
            return None
        if not (
            href.startswith("./")
            or (href.startswith("/") and not href.startswith("//"))
        ):
            return None
        if "\\" in href or any(part == ".." for part in href.split("/")):
            return None
        resolved = urljoin(self._website_base + "/", href)
        candidate = urlsplit(resolved)
        expected = urlsplit(self._website_base)
        if (
            candidate.scheme != expected.scheme
            or candidate.netloc != expected.netloc
            or not candidate.path.startswith(expected.path.rstrip("/") + "/")
        ):
            return None
        return resolved

    def _consume_rate_limit(self) -> None:
        now = time.monotonic()
        self._request_times = [
            value for value in self._request_times if value > now - 60
        ]
        if len(self._request_times) >= self.MAX_REQUESTS_PER_MINUTE:
            raise SiteGatewayError("Public assistant request limit reached")
        self._request_times.append(now)

    @classmethod
    async def _read_response_body(cls, response: httpx.Response) -> bytes:
        declared_length = response.headers.get("content-length")
        if (
            declared_length
            and declared_length.isdigit()
            and int(declared_length) > cls.MAX_RESPONSE_BODY_BYTES
        ):
            raise SiteGatewayError(
                "Public website assistant response exceeded its size limit"
            )

        response_body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=4096):
            if len(response_body) + len(chunk) > cls.MAX_RESPONSE_BODY_BYTES:
                raise SiteGatewayError(
                    "Public website assistant response exceeded its size limit"
                )
            response_body.extend(chunk)
        return bytes(response_body)

    async def ask(self, message: str) -> PublicAnswer:
        normalized = message.strip()
        if not normalized or len(normalized) > self.MAX_MESSAGE_LENGTH:
            raise ValueError("Public assistant message must contain 1 to 1200 characters")
        self._consume_rate_limit()
        try:
            async with self._client.stream(
                "POST",
                "v1/assistant/chat",
                json={"message": normalized},
            ) as response:
                response.raise_for_status()
                payload = json.loads(await self._read_response_body(response))
        except (httpx.HTTPError, ValueError):
            raise SiteGatewayError(
                "Public website assistant is temporarily unavailable"
            ) from None

        if not isinstance(payload, dict):
            raise SiteGatewayError("Public website assistant returned an invalid response")
        answer = payload.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise SiteGatewayError("Public website assistant returned no answer")
        mode = payload.get("mode")
        if mode not in {"ai_grounded", "knowledge_fallback"}:
            mode = "public_gateway"
        handoff = payload.get("handoff") is True
        raw_sources = payload.get("sources")
        sources: list[PublicSource] = []
        if isinstance(raw_sources, list):
            for item in raw_sources[: self.MAX_SOURCES]:
                if not isinstance(item, dict):
                    continue
                title = item.get("title")
                url = self._source_url(item.get("href"))
                if isinstance(title, str) and title.strip() and url:
                    sources.append(PublicSource(title=title.strip()[:100], url=url))
        return PublicAnswer(
            answer=answer.strip()[: self.MAX_RESPONSE_LENGTH],
            mode=mode,
            handoff=handoff,
            sources=tuple(sources),
        )

    @staticmethod
    def format_answer(result: PublicAnswer) -> str:
        if result.handoff:
            prefix = "منبع تأییدشده‌ای برای پاسخ پیدا نشد؛ پاسخ حدسی ارائه نمی‌کنم."
        elif result.mode == "knowledge_fallback":
            prefix = "پاسخ از دانش تأییدشدهٔ سایت (بدون استنتاج مدل زبانی):"
        elif result.mode == "ai_grounded":
            prefix = "پاسخ هوش مصنوعی مبتنی بر منابع تأییدشدهٔ سایت:"
        else:
            prefix = "پاسخ از درگاه عمومی سایت:"
        lines = [prefix, "", result.answer]
        if result.sources:
            lines.extend(["", "منابع:"])
            lines.extend(f"• {source.title}: {source.url}" for source in result.sources)
        return "\n".join(lines)[:4096]

    async def close(self) -> None:
        await self._client.aclose()
