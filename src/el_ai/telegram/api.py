"""Small, secret-safe asynchronous Telegram Bot API client."""

from __future__ import annotations

import re
from typing import Any

import httpx


class TelegramAPIError(RuntimeError):
    """Sanitized Telegram API failure; never contains token or response body."""


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
        if not text or len(text) > 4096:
            raise ValueError("Telegram message must contain 1 to 4096 characters")
        return await self.call("sendMessage", {"chat_id": chat_id, "text": text})

    async def close(self) -> None:
        await self._client.aclose()
