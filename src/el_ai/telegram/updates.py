"""Bounded validation for Telegram polling update envelopes."""

from __future__ import annotations

from dataclasses import dataclass


class TelegramUpdateError(RuntimeError):
    """Raised when an update cannot be safely acknowledged."""


@dataclass(frozen=True)
class ParsedTelegramUpdate:
    update_id: int
    message: dict[str, object] | None
    ignored_reason: str | None = None


_CHAT_TYPES = frozenset({"private", "group", "supergroup", "channel"})
_MAX_TELEGRAM_ID = (1 << 63) - 1
_MAX_TEXT_UNITS = 4096


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def parse_telegram_update(value: object) -> ParsedTelegramUpdate:
    """Project only the message fields used by the bot and ignore unsupported variants."""
    if not isinstance(value, dict):
        raise TelegramUpdateError("Telegram update envelope must be an object")
    update_id = value.get("update_id")
    if not _is_integer(update_id) or update_id < 0:
        raise TelegramUpdateError("Telegram update ID is invalid")

    raw_message = value.get("message")
    if raw_message is None:
        return ParsedTelegramUpdate(update_id, None, "unsupported_update")
    if not isinstance(raw_message, dict):
        return ParsedTelegramUpdate(update_id, None, "invalid_message")

    chat = raw_message.get("chat")
    if not isinstance(chat, dict):
        return ParsedTelegramUpdate(update_id, None, "invalid_chat")
    chat_id = chat.get("id")
    chat_type = chat.get("type")
    if (
        not _is_integer(chat_id)
        or chat_id == 0
        or abs(chat_id) > _MAX_TELEGRAM_ID
        or not isinstance(chat_type, str)
        or chat_type not in _CHAT_TYPES
    ):
        return ParsedTelegramUpdate(update_id, None, "invalid_chat")
    if (chat_type == "private" and chat_id < 0) or (
        chat_type in {"group", "supergroup", "channel"} and chat_id > 0
    ):
        return ParsedTelegramUpdate(update_id, None, "invalid_chat")

    normalized: dict[str, object] = {
        "chat": {"id": chat_id, "type": chat_type}
    }
    sender = raw_message.get("from")
    if sender is not None:
        if not isinstance(sender, dict):
            return ParsedTelegramUpdate(update_id, None, "invalid_sender")
        user_id = sender.get("id")
        if not _is_integer(user_id) or user_id <= 0 or user_id > _MAX_TELEGRAM_ID:
            return ParsedTelegramUpdate(update_id, None, "invalid_sender")
        normalized["from"] = {"id": user_id}

    text = raw_message.get("text")
    if text is not None:
        if not isinstance(text, str) or _utf16_length(text) > _MAX_TEXT_UNITS:
            return ParsedTelegramUpdate(update_id, None, "invalid_text")
        normalized["text"] = text

    return ParsedTelegramUpdate(update_id, normalized)
