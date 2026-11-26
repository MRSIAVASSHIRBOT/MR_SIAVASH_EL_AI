import pytest

from el_ai.telegram.business_connections import BusinessConnectionStore
from el_ai.telegram.business_processor import BusinessEventProcessor
from el_ai.telegram.updates import parse_telegram_update


def connection_update(
    update_id=1,
    *,
    business_user_id=123,
    is_enabled=True,
    rights=None,
):
    return parse_telegram_update({
        "update_id": update_id,
        "business_connection": {
            "id": "business-123",
            "user": {"id": business_user_id},
            "user_chat_id": business_user_id,
            "date": 1_800_000_000,
            "is_enabled": is_enabled,
            "rights": (
                {"can_read_messages": True, "can_reply": True}
                if rights is None else rights
            ),
        },
    })


def customer_update(
    update_id=2,
    *,
    connection_id="business-123",
    customer_id=456,
    is_bot=False,
    is_from_business=False,
    event_type="business_message",
):
    field = event_type
    return parse_telegram_update({
        "update_id": update_id,
        field: {
            "business_connection_id": connection_id,
            "message_id": 77,
            "date": 1_800_000_100,
            "chat": {"id": customer_id, "type": "private"},
            "from": {"id": customer_id, "is_bot": is_bot},
            "is_from_business_account": is_from_business,
            "text": "سؤال مشتری",
        },
    })


def test_processor_persists_connection_update_for_configured_owner(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    processor = BusinessEventProcessor(store, frozenset({123}))

    result = processor.process(connection_update(10), now=1_800_000_101)

    assert result.status == "connection_enabled"
    assert result.event_type == "business_connection"
    assert result.update_id == 10
    assert result.connection is not None
    assert result.connection.business_user_id == 123
    assert result.actor is None
    assert store.get("business-123") == result.connection


def test_processor_accepts_zero_valued_telegram_update_id(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    processor = BusinessEventProcessor(store, frozenset({123}))

    result = processor.process(connection_update(0), now=1_800_000_101)

    assert result.status == "connection_enabled"
    assert result.connection is not None
    assert result.connection.last_update_id == 0


def test_processor_persists_disabled_connection_and_does_not_delete_it(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    processor = BusinessEventProcessor(store, frozenset({123}))
    processor.process(connection_update(10), now=1_800_000_101)

    result = processor.process(
        connection_update(11, is_enabled=False, rights={}),
        now=1_800_000_102,
    )

    assert result.status == "connection_disabled"
    assert result.connection is not None
    assert result.connection.is_enabled is False
    assert store.get("business-123") is not None


def test_processor_rejects_business_connection_not_owned_by_configured_owner(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    processor = BusinessEventProcessor(store, frozenset({123}))

    result = processor.process(
        connection_update(10, business_user_id=999),
        now=1_800_000_101,
    )

    assert result.status == "rejected"
    assert result.reason == "business_account_not_owner"
    assert result.connection is None
    assert store.load() == ()


def test_processor_resolves_customer_actor_without_granting_capabilities(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    processor = BusinessEventProcessor(store, frozenset({123}))
    processor.process(connection_update(10), now=1_800_000_101)

    result = processor.process(customer_update(11), now=1_800_000_102)

    assert result.status == "customer_context_resolved"
    assert result.actor is not None
    assert result.actor.actor_type == "business_customer"
    assert result.actor.business_user_id == 123
    assert result.actor.customer_user_id == 456
    assert result.actor.can_reply is True
    assert result.actor.capabilities == ()
    assert result.connection is None


def test_processor_resolves_read_only_actor_when_can_reply_is_missing(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    processor = BusinessEventProcessor(store, frozenset({123}))
    processor.process(
        connection_update(10, rights={"can_read_messages": True}),
        now=1_800_000_101,
    )

    result = processor.process(customer_update(11), now=1_800_000_102)

    assert result.status == "customer_context_resolved"
    assert result.actor is not None
    assert result.actor.can_reply is False
    assert result.actor.capabilities == ()


def test_processor_ignores_customer_message_without_read_permission(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    processor = BusinessEventProcessor(store, frozenset({123}))
    processor.process(
        connection_update(10, rights={"can_reply": True}),
        now=1_800_000_101,
    )

    result = processor.process(customer_update(11), now=1_800_000_102)

    assert result.status == "ignored"
    assert result.reason == "connection_or_customer_not_authorized"
    assert result.actor is None


@pytest.mark.parametrize(
    "update",
    [
        customer_update(12, connection_id="unknown"),
        customer_update(12, is_bot=True),
        customer_update(12, customer_id=123),
        customer_update(12, is_from_business=True),
    ],
)
def test_processor_fails_closed_for_invalid_or_non_customer_updates(update, tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    processor = BusinessEventProcessor(store, frozenset({123}))
    processor.process(connection_update(10), now=1_800_000_101)

    result = processor.process(update, now=1_800_000_102)

    assert result.status == "ignored"
    assert result.actor is None


@pytest.mark.parametrize("event_type", ["edited_business_message", "deleted_business_messages"])
def test_processor_does_not_treat_edits_or_deletions_as_new_customer_requests(
    event_type, tmp_path
):
    store = BusinessConnectionStore(tmp_path / "business.json")
    processor = BusinessEventProcessor(store, frozenset({123}))
    processor.process(connection_update(10), now=1_800_000_101)

    if event_type == "edited_business_message":
        update = customer_update(11, event_type=event_type)
    else:
        update = parse_telegram_update({
            "update_id": 11,
            "deleted_business_messages": {
                "business_connection_id": "business-123",
                "chat": {"id": 456, "type": "private"},
                "message_ids": [77],
            },
        })

    result = processor.process(update, now=1_800_000_102)

    assert result.status == "ignored"
    assert result.reason == "non_new_business_message"
    assert result.actor is None


def test_processor_ignores_regular_messages_and_unsupported_updates(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    processor = BusinessEventProcessor(store, frozenset({123}))
    regular = parse_telegram_update({
        "update_id": 55,
        "message": {
            "chat": {"id": 456, "type": "private"},
            "from": {"id": 456},
            "text": "hello",
        },
    })
    unsupported = parse_telegram_update({"update_id": 56, "callback_query": {"id": "x"}})

    regular_result = processor.process(regular)
    unsupported_result = processor.process(unsupported)

    assert regular_result.status == "ignored"
    assert regular_result.reason == "not_business_event"
    assert unsupported_result.status == "ignored"
    assert unsupported_result.reason == "unsupported_update"


def test_processor_requires_parsed_updates_and_valid_owner_configuration(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    with pytest.raises(ValueError):
        BusinessEventProcessor(store, {True})
    with pytest.raises(ValueError):
        BusinessEventProcessor(store, [123])
    processor = BusinessEventProcessor(store, frozenset({123}))
    with pytest.raises(TypeError):
        processor.process({"update_id": 1})


def test_processor_does_not_acknowledge_storage_failures(tmp_path, monkeypatch):
    store = BusinessConnectionStore(tmp_path / "business.json")
    processor = BusinessEventProcessor(store, frozenset({123}))

    def fail_save():
        raise RuntimeError("simulated disk failure")

    monkeypatch.setattr(store, "_save", fail_save)
    with pytest.raises(RuntimeError, match="simulated disk failure"):
        processor.process(connection_update(10), now=1_800_000_101)
    assert store.get("business-123") is None
