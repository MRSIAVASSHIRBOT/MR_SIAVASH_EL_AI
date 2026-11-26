"""Bounded validation for Telegram polling and Business update envelopes.

Business events are projected into a versioned, privacy-minimized payload. Raw Telegram
objects, file identifiers and unrecognized rights are never forwarded to application code.
"""

from __future__ import annotations

from dataclasses import dataclass


class TelegramUpdateError(RuntimeError):
    """Raised when an update cannot be safely acknowledged."""


@dataclass(frozen=True)
class ParsedTelegramUpdate:
    update_id: int
    message: dict[str, object] | None
    ignored_reason: str | None = None
    event_type: str = "unsupported"
    business_event: dict[str, object] | None = None


_CHAT_TYPES = frozenset({"private", "group", "supergroup", "channel"})
_MAX_TELEGRAM_ID = (1 << 63) - 1
_MAX_TELEGRAM_SAFE_INTEGER = (1 << 53) - 1
_MAX_TEXT_UNITS = 4096
_MAX_BUSINESS_CONNECTION_ID_LENGTH = 256
_MAX_DELETED_MESSAGE_IDS = 1000
_BUSINESS_CONTRACT_VERSION = "telegram-business-event.v1"

_BUSINESS_RIGHTS = frozenset(
    {
        "can_reply",
        "can_read_messages",
        "can_delete_sent_messages",
        "can_delete_all_messages",
        "can_edit_name",
        "can_edit_bio",
        "can_edit_profile_photo",
        "can_edit_username",
        "can_change_gift_settings",
        "can_view_gifts_and_stars",
        "can_convert_gifts_to_stars",
        "can_transfer_and_upgrade_gifts",
        "can_transfer_stars",
        "can_manage_stories",
    }
)

_BUSINESS_MEDIA_FIELDS = (
    "animation",
    "audio",
    "document",
    "paid_media",
    "photo",
    "sticker",
    "video",
    "video_note",
    "voice",
    "live_photo",
    "contact",
    "location",
    "venue",
    "poll",
    "dice",
)

_RECOGNIZED_UPDATE_FIELDS = (
    "message",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
)


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_integer(value: object, *, maximum: int = _MAX_TELEGRAM_ID) -> bool:
    return _is_integer(value) and 0 < value <= maximum


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _valid_connection_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= _MAX_BUSINESS_CONNECTION_ID_LENGTH
        and value == value.strip()
        and not any(character.isspace() for character in value)
    )


def _normalize_chat(
    value: object, *, private_only: bool = False
) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    chat_id = value.get("id")
    chat_type = value.get("type")
    if (
        not _is_integer(chat_id)
        or chat_id == 0
        or abs(chat_id) > _MAX_TELEGRAM_ID
        or not isinstance(chat_type, str)
        or chat_type not in _CHAT_TYPES
    ):
        return None
    if (chat_type == "private" and chat_id < 0) or (
        chat_type in {"group", "supergroup", "channel"} and chat_id > 0
    ):
        return None
    if private_only and chat_type != "private":
        return None
    return {"id": chat_id, "type": chat_type}


def _normalize_business_connection(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None

    connection_id = value.get("id")
    user = value.get("user")
    if (
        not _valid_connection_id(connection_id)
        or not isinstance(user, dict)
        or not _positive_integer(user.get("id"))
        or not _positive_integer(
            value.get("user_chat_id"), maximum=_MAX_TELEGRAM_SAFE_INTEGER
        )
        or not _positive_integer(value.get("date"))
        or not isinstance(value.get("is_enabled"), bool)
    ):
        return None

    raw_rights = value.get("rights", {})
    if not isinstance(raw_rights, dict):
        return None
    rights: dict[str, bool] = {}
    for key, enabled in raw_rights.items():
        if key in _BUSINESS_RIGHTS:
            if not isinstance(enabled, bool):
                return None
            rights[key] = enabled

    return {
        "contract_version": _BUSINESS_CONTRACT_VERSION,
        "event_type": "business_connection",
        "connection_id": connection_id,
        "business_user_id": user["id"],
        "user_chat_id": value["user_chat_id"],
        "connected_at": value["date"],
        "is_enabled": value["is_enabled"],
        "rights": rights,
    }


def _normalize_business_message(
    value: object, *, event_type: str
) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None

    connection_id = value.get("business_connection_id")
    message_id = value.get("message_id")
    message_date = value.get("date")
    chat = _normalize_chat(value.get("chat"), private_only=True)
    if (
        not _valid_connection_id(connection_id)
        or not _positive_integer(message_id)
        or not _positive_integer(message_date)
        or chat is None
    ):
        return None

    normalized_message: dict[str, object] = {
        "message_id": message_id,
        "date": message_date,
        "chat": chat,
    }

    sender = value.get("from")
    if not isinstance(sender, dict) or not _positive_integer(sender.get("id")):
        return None
    normalized_sender: dict[str, object] = {"id": sender["id"]}
    is_bot = sender.get("is_bot")
    if is_bot is not None:
        if not isinstance(is_bot, bool):
            return None
        normalized_sender["is_bot"] = is_bot
    normalized_message["sender"] = normalized_sender

    text = value.get("text")
    caption = value.get("caption")
    if text is not None and caption is not None:
        return None
    for field_name, field_value in (("text", text), ("caption", caption)):
        if field_value is None:
            continue
        if not isinstance(field_value, str):
            return None
        try:
            text_units = _utf16_length(field_value)
        except UnicodeEncodeError:
            return None
        if text_units > _MAX_TEXT_UNITS:
            return None
        normalized_message[field_name] = field_value

    media_kind = next(
        (
            field_name
            for field_name in _BUSINESS_MEDIA_FIELDS
            if value.get(field_name) is not None
        ),
        None,
    )
    if media_kind is not None:
        normalized_message["media_kind"] = media_kind

    return {
        "contract_version": _BUSINESS_CONTRACT_VERSION,
        "event_type": event_type,
        "connection_id": connection_id,
        "message": normalized_message,
    }


def _normalize_deleted_business_messages(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    connection_id = value.get("business_connection_id")
    chat = _normalize_chat(value.get("chat"), private_only=True)
    message_ids = value.get("message_ids")
    if (
        not _valid_connection_id(connection_id)
        or chat is None
        or not isinstance(message_ids, list)
        or not 1 <= len(message_ids) <= _MAX_DELETED_MESSAGE_IDS
        or any(not _positive_integer(item) for item in message_ids)
        or len(set(message_ids)) != len(message_ids)
    ):
        return None
    return {
        "contract_version": _BUSINESS_CONTRACT_VERSION,
        "event_type": "deleted_business_messages",
        "connection_id": connection_id,
        "chat": chat,
        "message_ids": list(message_ids),
    }


def _normalize_regular_message(
    value: object,
) -> tuple[dict[str, object] | None, str | None]:
    if not isinstance(value, dict):
        return None, "invalid_message"
    chat = _normalize_chat(value.get("chat"))
    if chat is None:
        return None, "invalid_chat"

    normalized: dict[str, object] = {"chat": chat}
    sender = value.get("from")
    if sender is not None:
        if not isinstance(sender, dict):
            return None, "invalid_sender"
        user_id = sender.get("id")
        if not _positive_integer(user_id):
            return None, "invalid_sender"
        normalized["from"] = {"id": user_id}

    text = value.get("text")
    if text is not None:
        if not isinstance(text, str):
            return None, "invalid_text"
        try:
            text_units = _utf16_length(text)
        except UnicodeEncodeError:
            return None, "invalid_text"
        if text_units > _MAX_TEXT_UNITS:
            return None, "invalid_text"
        normalized["text"] = text

    return normalized, None


def parse_telegram_update(value: object) -> ParsedTelegramUpdate:
    """Validate one Telegram update and return a bounded, typed projection.

    Existing regular-message semantics and error reasons remain unchanged. Business
    variants are returned separately so they cannot enter the current owner/public
    MessageRouter by accident.
    """
    if not isinstance(value, dict):
        raise TelegramUpdateError("Telegram update envelope must be an object")
    update_id = value.get("update_id")
    if (
        not _is_integer(update_id)
        or update_id < 0
        or update_id > _MAX_TELEGRAM_ID
    ):
        raise TelegramUpdateError("Telegram update ID is invalid")

    present_fields = [field for field in _RECOGNIZED_UPDATE_FIELDS if field in value]
    if len(present_fields) > 1:
        raise TelegramUpdateError("Telegram update contains multiple event variants")
    if not present_fields:
        return ParsedTelegramUpdate(update_id, None, "unsupported_update")

    field_name = present_fields[0]
    if field_name == "message":
        normalized, ignored_reason = _normalize_regular_message(value.get(field_name))
        if normalized is None:
            return ParsedTelegramUpdate(
                update_id,
                None,
                ignored_reason or "invalid_message",
                event_type="message",
            )
        return ParsedTelegramUpdate(update_id, normalized, event_type="message")

    if field_name == "business_connection":
        normalized_event = _normalize_business_connection(value.get(field_name))
    elif field_name in {"business_message", "edited_business_message"}:
        normalized_event = _normalize_business_message(
            value.get(field_name), event_type=field_name
        )
    else:
        normalized_event = _normalize_deleted_business_messages(value.get(field_name))

    if normalized_event is None:
        return ParsedTelegramUpdate(
            update_id,
            None,
            f"invalid_{field_name}",
            event_type=field_name,
        )

    return ParsedTelegramUpdate(
        update_id,
        None,
        event_type=field_name,
        business_event=normalized_event,
    )
