"""Minimal Telegram Bot API client with bounded requests and sanitized failures."""

from __future__ import annotations

from typing import Any

import httpx


class TelegramAPIError(RuntimeError):
    """A Telegram API failure that deliberately omits response bodies and URLs."""

    def __init__(
        self,
        method: str,
        *,
        status_code: int | None = None,
        error_code: int | None = None,
        reason: str = "api_error",
    ) -> None:
        details = [f"Telegram {method} failed", reason]
        if status_code is not None:
            details.append(f"HTTP {status_code}")
        if error_code is not None:
            details.append(f"API {error_code}")
        super().__init__("; ".join(details) + ".")


class TelegramClient:
    """Small async Bot API client; never logs the token-bearing request URL."""

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 35.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not token or any(character.isspace() for character in token):
            raise ValueError("A non-empty Telegram token is required.")
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = await self._client.post(f"{self._base_url}/{method}", json=payload)
        except httpx.HTTPError:
            raise TelegramAPIError(method, reason="transport_error") from None

        try:
            body = response.json()
        except ValueError:
            raise TelegramAPIError(
                method,
                status_code=response.status_code,
                reason="invalid_response",
            ) from None

        if not response.is_success or not isinstance(body, dict) or body.get("ok") is not True:
            raw_error_code = body.get("error_code") if isinstance(body, dict) else None
            error_code = raw_error_code if isinstance(raw_error_code, int) else None
            raise TelegramAPIError(
                method,
                status_code=response.status_code,
                error_code=error_code,
            )
        return body.get("result")

    async def get_updates(self, *, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        if not 0 <= timeout <= 50:
            raise ValueError("Telegram long-poll timeout must be between 0 and 50 seconds.")
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload)
        if not isinstance(result, list):
            raise TelegramAPIError("getUpdates", reason="invalid_result")
        return [item for item in result if isinstance(item, dict)]

    async def send_message(self, chat_id: int, text: str) -> dict[str, Any] | None:
        if not isinstance(chat_id, int) or chat_id <= 0:
            raise ValueError("A positive private-chat ID is required.")
        if not isinstance(text, str) or not text.strip() or len(text) > 4096:
            raise ValueError("Telegram message text must contain 1 to 4096 characters.")
        result = await self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        )
        return result if isinstance(result, dict) else None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> TelegramClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()
