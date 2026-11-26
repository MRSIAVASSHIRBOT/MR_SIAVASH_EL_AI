import os
import stat

import pytest

from el_ai.telegram.business_connections import (
    BusinessConnectionAuthorizationError,
    BusinessConnectionError,
    BusinessConnectionStore,
)


def connection_event(
    *,
    connection_id="biz-connection-1",
    business_user_id=123,
    is_enabled=True,
    rights=None,
    user_chat_id=123,
    connected_at=1_800_000_000,
):
    return {
        "contract_version": "telegram-business-event.v1",
        "event_type": "business_connection",
        "connection_id": connection_id,
        "business_user_id": business_user_id,
        "user_chat_id": user_chat_id,
        "connected_at": connected_at,
        "is_enabled": is_enabled,
        "rights": (
            {"can_read_messages": True, "can_reply": True}
            if rights is None else rights
        ),
    }


def business_message(
    *,
    connection_id="biz-connection-1",
    chat_id=456,
    customer_id=456,
    message_id=77,
    is_bot=False,
    is_from_business=False,
    event_type="business_message",
):
    return {
        "contract_version": "telegram-business-event.v1",
        "event_type": event_type,
        "connection_id": connection_id,
        "message": {
            "message_id": message_id,
            "date": 1_800_000_100,
            "chat": {"id": chat_id, "type": "private"},
            "sender": {"id": customer_id, "is_bot": is_bot},
            "is_from_business_account": is_from_business,
            "text": "سلام",
        },
    }


def test_registry_persists_owner_bound_connection_and_rights_snapshot(tmp_path):
    path = tmp_path / "state" / "business-connections.json"
    store = BusinessConnectionStore(path)

    connection = store.update_from_event(
        connection_event(),
        owner_ids=frozenset({123}),
        update_id=100,
        now=1_800_000_101,
    )

    assert connection.connection_id == "biz-connection-1"
    assert connection.business_user_id == 123
    assert connection.is_enabled is True
    assert connection.can_read_messages is True
    assert connection.can_reply is True
    assert path.exists()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    restored = BusinessConnectionStore(path)
    assert restored.load() == (connection,)
    assert restored.get("biz-connection-1") == connection
    assert restored.list_for_owner(123) == (connection,)
    assert restored.list_for_owner(999) == ()


def test_registry_rejects_connections_not_owned_by_configured_owner(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")

    with pytest.raises(BusinessConnectionAuthorizationError):
        store.update_from_event(
            connection_event(business_user_id=999),
            owner_ids=frozenset({123}),
            update_id=1,
            now=100,
        )

    assert store.load() == ()


def test_registry_rejects_missing_or_invalid_owner_configuration(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")

    with pytest.raises(BusinessConnectionError):
        store.update_from_event(
            connection_event(),
            owner_ids=frozenset(),
            update_id=1,
            now=100,
        )

    with pytest.raises(BusinessConnectionError):
        store.update_from_event(
            connection_event(),
            owner_ids={True},
            update_id=1,
            now=100,
        )


def test_registry_replaces_the_complete_rights_snapshot_and_ignores_stale_updates(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    first = store.update_from_event(
        connection_event(rights={"can_read_messages": True, "can_reply": True}),
        owner_ids=frozenset({123}),
        update_id=100,
        now=1000,
    )

    stale = store.update_from_event(
        connection_event(rights={"can_read_messages": False, "can_reply": False}),
        owner_ids=frozenset({123}),
        update_id=99,
        now=1001,
    )
    assert stale == first
    assert store.get("biz-connection-1") == first

    updated = store.update_from_event(
        connection_event(rights={"can_read_messages": True}),
        owner_ids=frozenset({123}),
        update_id=101,
        now=1002,
    )
    assert updated.last_update_id == 101
    assert updated.can_read_messages is True
    assert updated.can_reply is False
    assert dict(updated.rights) == {"can_read_messages": True}


def test_duplicate_update_id_is_idempotent_even_when_payload_differs(tmp_path):
    path = tmp_path / "business.json"
    store = BusinessConnectionStore(path)
    first = store.update_from_event(
        connection_event(),
        owner_ids=frozenset({123}),
        update_id=10,
        now=100,
    )
    before = path.read_bytes()

    duplicate = store.update_from_event(
        connection_event(is_enabled=False, rights={}),
        owner_ids=frozenset({123}),
        update_id=10,
        now=101,
    )

    assert duplicate == first
    assert path.read_bytes() == before


def test_disabled_connection_is_persisted_and_cannot_resolve_customer_actor(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    store.update_from_event(
        connection_event(is_enabled=False),
        owner_ids=frozenset({123}),
        update_id=1,
        now=100,
    )

    assert store.get("biz-connection-1").is_enabled is False
    assert store.resolve_customer_actor(business_message()) is None


def test_connection_update_replaces_missing_rights_as_false(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    store.update_from_event(
        connection_event(rights={"can_read_messages": True, "can_reply": True}),
        owner_ids=frozenset({123}),
        update_id=1,
        now=100,
    )
    updated = store.update_from_event(
        connection_event(rights={"can_read_messages": True}),
        owner_ids=frozenset({123}),
        update_id=2,
        now=101,
    )

    assert updated.can_read_messages is True
    assert updated.can_reply is False


def test_same_connection_id_cannot_be_rebound_to_another_owner(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    store.update_from_event(
        connection_event(business_user_id=123),
        owner_ids=frozenset({123, 999}),
        update_id=10,
        now=100,
    )

    with pytest.raises(BusinessConnectionAuthorizationError):
        store.update_from_event(
            connection_event(business_user_id=999),
            owner_ids=frozenset({123, 999}),
            update_id=11,
            now=101,
        )


@pytest.mark.parametrize(
    "event",
    [
        None,
        {},
        {**connection_event(), "unexpected": True},
        {**connection_event(), "business_user_id": True},
        {**connection_event(), "user_chat_id": True},
        {**connection_event(), "connected_at": False},
        {**connection_event(), "is_enabled": 1},
        {**connection_event(), "rights": {"can_reply": "yes"}},
        {**connection_event(), "rights": {"unknown_permission": True}},
        {**connection_event(), "connection_id": "  biz-connection-1"},
    ],
)
def test_registry_rejects_malformed_connection_event(event, tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    with pytest.raises(BusinessConnectionError):
        store.update_from_event(
            event,
            owner_ids=frozenset({123}),
            update_id=1,
            now=100,
        )


def test_registry_rejects_boolean_update_ids_and_invalid_time(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    with pytest.raises(BusinessConnectionError):
        store.update_from_event(
            connection_event(),
            owner_ids=frozenset({123}),
            update_id=True,
            now=100,
        )
    with pytest.raises(BusinessConnectionError):
        store.update_from_event(
            connection_event(),
            owner_ids=frozenset({123}),
            update_id=1,
            now=False,
        )


def test_registry_rejects_corrupt_and_unsupported_persisted_state(tmp_path):
    path = tmp_path / "business.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(BusinessConnectionError):
        BusinessConnectionStore(path).load()

    path.write_text('{"version": 2, "connections": []}', encoding="utf-8")
    with pytest.raises(BusinessConnectionError):
        BusinessConnectionStore(path).load()


def test_registry_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "business.json"
    path.write_text(
        '{"version": 1, "version": 1, "connections": []}',
        encoding="utf-8",
    )
    with pytest.raises(BusinessConnectionError):
        BusinessConnectionStore(path).load()


def test_registry_refuses_symlink_state_files(tmp_path):
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("O_NOFOLLOW is unavailable on this platform")
    target = tmp_path / "target.json"
    target.write_text('{"version": 1, "connections": []}', encoding="utf-8")
    link = tmp_path / "business.json"
    link.symlink_to(target)

    with pytest.raises(BusinessConnectionError):
        BusinessConnectionStore(link).load()


def test_registry_enforces_connection_count_limit(tmp_path, monkeypatch):
    import el_ai.telegram.business_connections as module

    monkeypatch.setattr(module, "MAX_BUSINESS_CONNECTIONS", 1)
    store = BusinessConnectionStore(tmp_path / "business.json")
    store.update_from_event(
        connection_event(connection_id="first"),
        owner_ids=frozenset({123}),
        update_id=1,
        now=100,
    )

    with pytest.raises(BusinessConnectionError, match="entry limit"):
        store.update_from_event(
            connection_event(connection_id="second"),
            owner_ids=frozenset({123}),
            update_id=2,
            now=101,
        )


def test_registry_lists_connections_only_for_exact_owner(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    store.update_from_event(
        connection_event(connection_id="owner-a", business_user_id=123),
        owner_ids=frozenset({123, 456}),
        update_id=1,
        now=100,
    )
    store.update_from_event(
        connection_event(connection_id="owner-b", business_user_id=456),
        owner_ids=frozenset({123, 456}),
        update_id=2,
        now=101,
    )

    assert [item.connection_id for item in store.list_for_owner(123)] == ["owner-a"]
    assert [item.connection_id for item in store.list_for_owner(456)] == ["owner-b"]


def test_actor_resolution_requires_enabled_readable_connection_and_returns_no_capabilities(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    store.update_from_event(
        connection_event(rights={"can_read_messages": True, "can_reply": True}),
        owner_ids=frozenset({123}),
        update_id=1,
        now=100,
    )

    actor = store.resolve_customer_actor(business_message())

    assert actor is not None
    assert actor.actor_type == "business_customer"
    assert actor.business_user_id == 123
    assert actor.customer_user_id == 456
    assert actor.chat_id == 456
    assert actor.message_id == 77
    assert actor.can_reply is True
    assert actor.capabilities == ()


def test_actor_can_be_resolved_read_only_when_reply_right_is_missing(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    store.update_from_event(
        connection_event(rights={"can_read_messages": True}),
        owner_ids=frozenset({123}),
        update_id=1,
        now=100,
    )

    actor = store.resolve_customer_actor(business_message())

    assert actor is not None
    assert actor.can_reply is False
    assert actor.capabilities == ()


@pytest.mark.parametrize(
    "rights",
    [
        {},
        {"can_reply": True},
        {"can_read_messages": False, "can_reply": True},
    ],
)
def test_actor_resolution_requires_read_right(rights, tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    store.update_from_event(
        connection_event(rights=rights),
        owner_ids=frozenset({123}),
        update_id=1,
        now=100,
    )

    assert store.resolve_customer_actor(business_message()) is None


@pytest.mark.parametrize(
    "event",
    [
        business_message(connection_id="unknown-connection"),
        business_message(is_bot=True),
        business_message(customer_id=123),
        business_message(is_from_business=True),
        business_message(chat_id=-456),
        business_message(event_type="edited_business_message"),
        {
            "contract_version": "telegram-business-event.v1",
            "event_type": "deleted_business_messages",
            "connection_id": "biz-connection-1",
            "chat": {"id": 456, "type": "private"},
            "message_ids": [77],
        },
    ],
)
def test_actor_resolution_fails_closed_for_wrong_actor_or_event(event, tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    store.update_from_event(
        connection_event(rights={"can_read_messages": True, "can_reply": True}),
        owner_ids=frozenset({123}),
        update_id=1,
        now=100,
    )

    assert store.resolve_customer_actor(event) is None


def test_actor_resolution_rejects_invalid_shapes_and_bot_flag_types(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    store.update_from_event(
        connection_event(),
        owner_ids=frozenset({123}),
        update_id=1,
        now=100,
    )

    invalid = business_message()
    invalid["message"]["sender"]["is_bot"] = "false"
    assert store.resolve_customer_actor(invalid) is None
    assert store.resolve_customer_actor({"event_type": "business_message"}) is None
    assert store.resolve_customer_actor(None) is None


def test_parser_connection_and_message_feed_owner_bound_registry(tmp_path):
    from el_ai.telegram.updates import parse_telegram_update

    store = BusinessConnectionStore(tmp_path / "business.json")
    parsed_connection = parse_telegram_update({
        "update_id": 201,
        "business_connection": {
            "id": "biz-connection-1",
            "user": {"id": 123},
            "user_chat_id": 123,
            "date": 1_800_000_000,
            "is_enabled": True,
            "rights": {"can_read_messages": True, "can_reply": True},
        },
    })
    assert parsed_connection.business_event is not None
    store.update_from_event(
        parsed_connection.business_event,
        owner_ids=frozenset({123}),
        update_id=parsed_connection.update_id,
        now=1_800_000_010,
    )

    parsed_message = parse_telegram_update({
        "update_id": 202,
        "business_message": {
            "business_connection_id": "biz-connection-1",
            "message_id": 77,
            "date": 1_800_000_010,
            "chat": {"id": 456, "type": "private"},
            "from": {"id": 456, "is_bot": False},
            "is_from_business_account": False,
            "text": "سلام",
        },
    })
    assert parsed_message.business_event is not None
    actor = store.resolve_customer_actor(parsed_message.business_event)

    assert actor is not None
    assert actor.customer_user_id == 456
    assert actor.connection_id == "biz-connection-1"
    assert actor.capabilities == ()


def test_parser_direction_flag_prevents_business_outbound_message_from_becoming_customer_actor(tmp_path):
    from el_ai.telegram.updates import parse_telegram_update

    store = BusinessConnectionStore(tmp_path / "business.json")
    store.update_from_event(
        connection_event(),
        owner_ids=frozenset({123}),
        update_id=1,
        now=100,
    )
    parsed = parse_telegram_update({
        "update_id": 2,
        "business_message": {
            "business_connection_id": "biz-connection-1",
            "message_id": 78,
            "date": 1_800_000_010,
            "chat": {"id": 456, "type": "private"},
            "from": {"id": 456, "is_bot": False},
            "is_from_business_account": True,
            "text": "پیام ارسال‌شده توسط حساب تجاری",
        },
    })
    assert parsed.business_event is not None
    assert store.resolve_customer_actor(parsed.business_event) is None


def test_actor_resolution_rejects_unrecognized_message_fields(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    store.update_from_event(
        connection_event(),
        owner_ids=frozenset({123}),
        update_id=1,
        now=100,
    )
    event = business_message()
    event["message"]["username"] = "not-normalized"
    assert store.resolve_customer_actor(event) is None


def test_actor_resolution_requires_message_date_and_rejects_boolean_date(tmp_path):
    store = BusinessConnectionStore(tmp_path / "business.json")
    store.update_from_event(
        connection_event(),
        owner_ids=frozenset({123}),
        update_id=1,
        now=100,
    )
    missing_date = business_message()
    missing_date["message"].pop("date")
    assert store.resolve_customer_actor(missing_date) is None

    boolean_date = business_message()
    boolean_date["message"]["date"] = True
    assert store.resolve_customer_actor(boolean_date) is None
