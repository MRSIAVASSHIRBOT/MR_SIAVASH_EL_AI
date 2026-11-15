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
    assert parsed.message == {
        "chat": {"id": 123, "type": "private"},
        "from": {"id": 123},
        "text": "/help",
    }
    assert parsed.ignored_reason is None


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
            "text": "\\ud800",
        },
    })
    assert parsed.message is None
    assert parsed.ignored_reason == "invalid_text"
