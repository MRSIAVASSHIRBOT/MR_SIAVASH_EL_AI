"""Small, secret-safe asynchronous Telegram Bot API client."""

from __future__ import annotations

import re
from typing import Any

import httpx


class TelegramAPIError(RuntimeError):
    """Sanitized Telegram API failure; never contains token or response body."""


def _utf16_length(value: str) -> int:
    """Return the UTF-16 code-unit count used by Telegram message limits."""
    return len(value.encode("utf-16-le")) // 2


def split_telegram_message(text: str, *, max_units: int = 4096) -> list[str]:
    """Split text into Telegram-safe chunks without dropping or duplicating content."""
    if not isinstance(text, str):
        raise TypeError("Telegram message text must be a string")
    if isinstance(max_units, bool) or not isinstance(max_units, int) or max_units < 1:
        raise ValueError("Telegram message limit must be a positive integer")
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        units = 0
        end = start
        last_whitespace_boundary: int | None = None
        while end < len(text):
            width = 2 if ord(text[end]) > 0xFFFF else 1
            if units + width > max_units:
                break
            units += width
            if text[end].isspace():
                last_whitespace_boundary = end + 1
            end += 1

        if end == start:
            raise ValueError("Telegram message limit is too small for the next character")
        if end < len(text) and last_whitespace_boundary is not None:
            if last_whitespace_boundary > start:
                end = last_whitespace_boundary

        chunks.append(text[start:end])
        start = end

    return chunks


class TelegramBotAPI:
    def __init__(
        self,
        token: str,
        *,
        timeout_seconds: int = 35,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{10,}", token):
            raise ValueError("Telegram bot token is missing or malformed")
        self._client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}/",
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def call(self, method: str, payload: dict[str, Any]) -> Any:
        if not method.isascii() or not method.replace("_", "").isalnum():
            raise ValueError("Invalid Telegram API method")
        try:
            response = await self._client.post(method, json=payload)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            # HTTPX exceptions retain a request URL containing the bot token.
            failed = True
        else:
            failed = False
        if failed:
            raise TelegramAPIError("Telegram API request failed")
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise TelegramAPIError("Telegram API rejected the request")
        return data.get("result")

    async def get_updates(self, *, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = await self.call("getUpdates", payload)
        if not isinstance(result, list):
            raise TelegramAPIError("Telegram returned an invalid update collection")
        return [item for item in result if isinstance(item, dict)]

    async def send_message(self, chat_id: int, text: str) -> Any:
        if not text or _utf16_length(text) > 4096:
            raise ValueError("Telegram message must contain 1 to 4096 UTF-16 code units")
        return await self.call("sendMessage", {"chat_id": chat_id, "text": text})

    async def close(self) -> None:
        await self._client.aclose()
