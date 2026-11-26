import pytest

from el_ai.telegram.updates import TelegramUpdateError, parse_telegram_update


def test_parser_projects_only_bounded_message_fields():
    parsed = parse_telegram_update({
        "update_id": 12,
        "message": {
            "message_id": 99,
            "chat": {"id": 123, "type": "private", "title": "ignored"},
            "from": {"id": 123, "username": "ignored"},
            "text": "/help",
            "unexpected": "ignored",
        },
    })
    assert parsed.update_id == 12
    assert parsed.event_type == "message"
    assert parsed.message == {
        "chat": {"id": 123, "type": "private"},
        "from": {"id": 123},
        "text": "/help",
    }
    assert parsed.ignored_reason is None
    assert parsed.business_event is None


def test_parser_keeps_valid_non_text_messages_for_the_router():
    parsed = parse_telegram_update({
        "update_id": 15,
        "message": {
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 123},
            "photo": [{"file_id": "opaque"}],
        },
    })
    assert parsed.message == {
        "chat": {"id": 123, "type": "private"},
        "from": {"id": 123},
    }
    assert parsed.ignored_reason is None


def test_parser_preserves_acknowledgeable_unsupported_update_ids():
    parsed = parse_telegram_update({"update_id": 13, "callback_query": {"id": "opaque"}})
    assert parsed.update_id == 13
    assert parsed.message is None
    assert parsed.ignored_reason == "unsupported_update"
    assert parsed.event_type == "unsupported"


@pytest.mark.parametrize(
    "message",
    [
        None,
        {"chat": {"id": True, "type": "private"}, "text": "hello"},
        {"chat": {"id": 123, "type": "unknown"}, "text": "hello"},
        {"chat": {"id": 123, "type": "private"}, "from": {"id": True}, "text": "hello"},
        {"chat": {"id": 123, "type": "private"}, "text": 42},
        {"chat": {"id": 123, "type": "private"}, "text": "😀" * 2049},
    ],
)
def test_parser_ignores_malformed_nested_message_without_losing_update_id(message):
    parsed = parse_telegram_update({"update_id": 14, "message": message})
    assert parsed.update_id == 14
    assert parsed.message is None
    assert parsed.ignored_reason is not None


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"update_id": True},
        {"update_id": -1},
        {"update_id": "15"},
    ],
)
def test_parser_rejects_unacknowledgeable_update_envelopes(value):
    with pytest.raises(TelegramUpdateError):
        parse_telegram_update(value)


def test_parser_ignores_text_with_unpaired_surrogates():
    parsed = parse_telegram_update({
        "update_id": 16,
        "message": {
            "chat": {"id": 123, "type": "private"},
            "text": chr(0xD800),
        },
    })
    assert parsed.message is None
    assert parsed.ignored_reason == "invalid_text"


def _business_message(**overrides):
    value = {
        "business_connection_id": "connection-123",
        "message_id": 51,
        "date": 1_800_000_000,
        "chat": {"id": 200, "type": "private", "first_name": "ignored"},
        "from": {"id": 300, "is_bot": False, "username": "ignored"},
        "text": "سلام",
    }
    value.update(overrides)
    return value


def test_business_connection_is_normalized_with_only_known_rights():
    parsed = parse_telegram_update({
        "update_id": 100,
        "business_connection": {
            "id": "connection-123",
            "user": {"id": 200, "username": "ignored"},
            "user_chat_id": 200,
            "date": 1_800_000_000,
            "is_enabled": True,
            "rights": {
                "can_reply": True,
                "can_read_messages": False,
                "future_unknown_permission": True,
            },
        },
    })
    assert parsed.event_type == "business_connection"
    assert parsed.message is None
    assert parsed.ignored_reason is None
    assert parsed.business_event == {
        "contract_version": "telegram-business-event.v1",
        "event_type": "business_connection",
        "connection_id": "connection-123",
        "business_user_id": 200,
        "user_chat_id": 200,
        "connected_at": 1_800_000_000,
        "is_enabled": True,
        "rights": {"can_reply": True, "can_read_messages": False},
    }


@pytest.mark.parametrize(
    "connection",
    [
        None,
        {"id": "", "user": {"id": 200}, "user_chat_id": 200, "date": 1, "is_enabled": True},
        {"id": "connection-123", "user": {"id": True}, "user_chat_id": 200, "date": 1, "is_enabled": True},
        {"id": "connection-123", "user": {"id": 200}, "user_chat_id": True, "date": 1, "is_enabled": True},
        {"id": "connection-123", "user": {"id": 200}, "user_chat_id": 200, "date": 1, "is_enabled": 1},
        {
            "id": "connection-123",
            "user": {"id": 200},
            "user_chat_id": 200,
            "date": 1,
            "is_enabled": True,
            "rights": {"can_reply": "true"},
        },
    ],
)
def test_malformed_business_connection_is_not_forwarded(connection):
    parsed = parse_telegram_update({"update_id": 101, "business_connection": connection})
    assert parsed.update_id == 101
    assert parsed.event_type == "business_connection"
    assert parsed.business_event is None
    assert parsed.ignored_reason == "invalid_business_connection"


def test_business_message_is_normalized_and_separated_from_owner_message():
    parsed = parse_telegram_update({
        "update_id": 102,
        "business_message": _business_message(),
    })
    assert parsed.event_type == "business_message"
    assert parsed.message is None
    assert parsed.ignored_reason is None
    assert parsed.business_event == {
        "contract_version": "telegram-business-event.v1",
        "event_type": "business_message",
        "connection_id": "connection-123",
        "message": {
            "message_id": 51,
            "date": 1_800_000_000,
            "chat": {"id": 200, "type": "private"},
            "sender": {"id": 300, "is_bot": False},
            "text": "سلام",
        },
    }


def test_edited_business_message_has_its_own_event_type():
    parsed = parse_telegram_update({
        "update_id": 103,
        "edited_business_message": _business_message(caption="نسخهٔ ویرایش‌شده", text=None),
    })
    assert parsed.event_type == "edited_business_message"
    assert parsed.business_event["event_type"] == "edited_business_message"
    assert parsed.business_event["message"]["caption"] == "نسخهٔ ویرایش‌شده"


def test_business_media_is_classified_without_exposing_file_ids():
    parsed = parse_telegram_update({
        "update_id": 104,
        "business_message": _business_message(
            text=None,
            photo=[{"file_id": "do-not-copy", "file_unique_id": "also-do-not-copy"}],
        ),
    })
    assert parsed.business_event["message"]["media_kind"] == "photo"
    assert "file_id" not in str(parsed.business_event)
    assert "file_unique_id" not in str(parsed.business_event)


@pytest.mark.parametrize(
    "message",
    [
        _business_message(chat={"id": -200, "type": "private"}),
        _business_message(chat={"id": 200, "type": "group"}),
        _business_message(business_connection_id=""),
        _business_message(message_id=True),
        _business_message(date=False),
        _business_message(text="😀" * 2049),
        _business_message(text=42),
        _business_message(text="both", caption="invalid"),
        _business_message(**{"from": {"id": True}}),
    ],
)
def test_invalid_business_message_is_not_forwarded(message):
    parsed = parse_telegram_update({"update_id": 105, "business_message": message})
    assert parsed.event_type == "business_message"
    assert parsed.business_event is None
    assert parsed.ignored_reason == "invalid_business_message"


def test_deleted_business_messages_are_normalized():
    parsed = parse_telegram_update({
        "update_id": 106,
        "deleted_business_messages": {
            "business_connection_id": "connection-123",
            "chat": {"id": 200, "type": "private"},
            "message_ids": [51, 52],
        },
    })
    assert parsed.event_type == "deleted_business_messages"
    assert parsed.business_event == {
        "contract_version": "telegram-business-event.v1",
        "event_type": "deleted_business_messages",
        "connection_id": "connection-123",
        "chat": {"id": 200, "type": "private"},
        "message_ids": [51, 52],
    }


@pytest.mark.parametrize(
    "deleted",
    [
        None,
        {"business_connection_id": "connection-123", "chat": {"id": 200, "type": "group"}, "message_ids": [1]},
        {"business_connection_id": "connection-123", "chat": {"id": 200, "type": "private"}, "message_ids": []},
        {"business_connection_id": "connection-123", "chat": {"id": 200, "type": "private"}, "message_ids": [1, True]},
        {"business_connection_id": "connection-123", "chat": {"id": 200, "type": "private"}, "message_ids": [1, 1]},
    ],
)
def test_invalid_deleted_business_messages_are_not_forwarded(deleted):
    parsed = parse_telegram_update({"update_id": 107, "deleted_business_messages": deleted})
    assert parsed.business_event is None
    assert parsed.ignored_reason == "invalid_deleted_business_messages"


def test_update_with_multiple_event_variants_is_rejected():
    with pytest.raises(TelegramUpdateError, match="multiple event variants"):
        parse_telegram_update({
            "update_id": 108,
            "message": {"chat": {"id": 123, "type": "private"}, "text": "hello"},
            "business_message": _business_message(),
        })


def test_business_events_never_populate_regular_message_field():
    parsed = parse_telegram_update({
        "update_id": 109,
        "business_message": _business_message(),
    })
    assert parsed.message is None
    assert parsed.business_event is not None
