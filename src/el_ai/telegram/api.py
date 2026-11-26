"""Small, secret-safe asynchronous Telegram Bot API client."""

from __future__ import annotations

import re
from typing import Any

import httpx


MAX_TELEGRAM_VIDEO_BYTES = 48_000_000
MAX_TELEGRAM_DOCUMENT_BYTES = 48_000_000


class TelegramAPIError(RuntimeError):
    """Sanitized Telegram API failure; never contains token or response body."""


def _utf16_length(value: str) -> int:
    """Return the UTF-16 code-unit count used by Telegram message limits.

    Telegram text is Unicode. Reject unpaired surrogate code points before any
    request is sent so callers get a stable validation error rather than a raw
    UnicodeEncodeError from the transport layer.
    """
    try:
        return len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise ValueError("Telegram text contains invalid Unicode") from exc


def split_telegram_message(text: str, *, max_units: int = 4096) -> list[str]:
    """Split text into Telegram-safe chunks without dropping or duplicating content."""
    if not isinstance(text, str):
        raise TypeError("Telegram message text must be a string")
    if isinstance(max_units, bool) or not isinstance(max_units, int) or max_units < 1:
        raise ValueError("Telegram message limit must be a positive integer")
    if not text:
        return []
    _utf16_length(text)  # Validate Unicode before chunking.

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
        payload: dict[str, Any] = {
            "timeout": timeout,
            "limit": 100,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self.call("getUpdates", payload)
        if (
            not isinstance(result, list)
            or len(result) > 100
            or any(not isinstance(item, dict) for item in result)
        ):
            raise TelegramAPIError("Telegram returned an invalid update collection")
        return result

    async def set_my_commands(
        self,
        commands: list[dict[str, str]],
        *,
        scope: dict[str, Any] | None = None,
    ) -> Any:
        """Set a bounded command menu for a public or explicitly scoped chat."""
        if not isinstance(commands, list) or len(commands) > 100:
            raise ValueError("Telegram command list must contain at most 100 entries")
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in commands:
            if not isinstance(item, dict) or set(item) != {"command", "description"}:
                raise ValueError("Telegram command entry is invalid")
            command = item["command"]
            description = item["description"]
            if (
                not isinstance(command, str)
                or not re.fullmatch(r"[a-z0-9_]{1,32}", command)
                or command in seen
                or not isinstance(description, str)
                or not description.strip()
                or len(description) > 256
            ):
                raise ValueError("Telegram command entry is invalid")
            seen.add(command)
            normalized.append({"command": command, "description": description})

        payload: dict[str, Any] = {"commands": normalized}
        if scope is not None:
            if not isinstance(scope, dict):
                raise ValueError("Telegram command scope is invalid")
            scope_type = scope.get("type")
            if isinstance(scope_type, str) and scope_type in {"default", "all_private_chats"} and set(scope) == {"type"}:
                payload["scope"] = {"type": scope_type}
            elif (
                scope_type == "chat"
                and set(scope) == {"type", "chat_id"}
                and isinstance(scope.get("chat_id"), int)
                and not isinstance(scope.get("chat_id"), bool)
                and scope["chat_id"] > 0
            ):
                payload["scope"] = {"type": "chat", "chat_id": scope["chat_id"]}
            else:
                raise ValueError("Telegram command scope is invalid")
        return await self.call("setMyCommands", payload)

    async def send_message(self, chat_id: int, text: str) -> Any:
        if not text or _utf16_length(text) > 4096:
            raise ValueError("Telegram message must contain 1 to 4096 UTF-16 code units")
        return await self.call("sendMessage", {"chat_id": chat_id, "text": text})


    async def send_video(
        self,
        chat_id: int,
        video: bytes,
        *,
        filename: str,
        caption: str | None = None,
    ) -> Any:
        """Upload a bounded MP4 using Telegram's multipart sendVideo method."""
        if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id <= 0:
            raise ValueError("Telegram chat ID must be a positive integer")
        if not isinstance(video, bytes) or not 12 <= len(video) <= MAX_TELEGRAM_VIDEO_BYTES:
            raise ValueError("Telegram video must be a non-empty MP4 within the upload limit")
        if not isinstance(filename, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.mp4", filename
        ):
            raise ValueError("Telegram video filename is invalid")
        if caption is not None and (
            not isinstance(caption, str) or _utf16_length(caption) > 1024
        ):
            raise ValueError("Telegram video caption exceeds the UTF-16 limit")
        if video[4:8] != b"ftyp":
            raise ValueError("Telegram video payload is not a valid MP4 container")

        data = {"chat_id": str(chat_id), "supports_streaming": "true"}
        if caption:
            data["caption"] = caption
        try:
            response = await self._client.post(
                "sendVideo",
                data=data,
                files={"video": (filename, video, "video/mp4")},
                timeout=httpx.Timeout(120),
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            # HTTPX exceptions retain a request URL containing the bot token.
            raise TelegramAPIError("Telegram video upload failed") from None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise TelegramAPIError("Telegram API rejected the video upload")
        return payload.get("result")

    async def send_document(
        self,
        chat_id: int,
        document: bytes,
        *,
        filename: str,
        caption: str | None = None,
    ) -> Any:
        """Upload a bounded CSV document using Telegram's multipart sendDocument method."""
        if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id <= 0:
            raise ValueError("Telegram chat ID must be a positive integer")
        if (
            not isinstance(document, bytes)
            or not 1 <= len(document) <= MAX_TELEGRAM_DOCUMENT_BYTES
        ):
            raise ValueError("Telegram document must be non-empty and within the upload limit")
        if not isinstance(filename, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.csv", filename
        ):
            raise ValueError("Telegram CSV filename is invalid")
        if caption is not None and (
            not isinstance(caption, str) or _utf16_length(caption) > 1024
        ):
            raise ValueError("Telegram document caption exceeds the UTF-16 limit")

        data = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        try:
            response = await self._client.post(
                "sendDocument",
                data=data,
                files={"document": (filename, document, "text/csv; charset=utf-8")},
                timeout=httpx.Timeout(120),
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            # HTTPX exceptions retain a request URL containing the bot token.
            raise TelegramAPIError("Telegram document upload failed") from None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise TelegramAPIError("Telegram API rejected the document upload")
        return payload.get("result")

    async def close(self) -> None:
        await self._client.aclose()
