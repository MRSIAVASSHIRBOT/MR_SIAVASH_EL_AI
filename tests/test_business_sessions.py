import base64
import json
import os
import stat

import pytest

from el_ai.integrations.secrets import SecretStoreError
from el_ai.telegram.business_connections import BusinessConnectionStore
from el_ai.telegram.business_processor import BusinessEventProcessor
from el_ai.telegram.business_sessions import (
    BUSINESS_SESSION_KEY_CREDENTIAL_ID,
    BusinessConversationStore,
    BusinessSessionAuthorizationError,
    BusinessSessionError,
    load_or_create_business_session_key,
)
from el_ai.telegram.updates import parse_telegram_update


class MemoryCredentialStore:
    def __init__(self):
        self.values = {}

    def load(self, client_id):
        return self.values.get(client_id)

    def save(self, client_id, credentials):
        self.values[client_id] = dict(credentials)

    def delete(self, client_id):
        self.values.pop(client_id, None)


class FailingCredentialStore:
    def load(self, _client_id):
        raise SecretStoreError("credential backend unavailable")

    def save(self, _client_id, _credentials):
        raise SecretStoreError("credential backend unavailable")

    def delete(self, _client_id):
        raise SecretStoreError("credential backend unavailable")


def _connection_update(update_id, *, connection_id, owner_id=123, rights=None, enabled=True):
    return parse_telegram_update({
        "update_id": update_id,
        "business_connection": {
            "id": connection_id,
            "user": {"id": owner_id},
            "user_chat_id": owner_id,
            "date": 1_800_000_000,
            "is_enabled": enabled,
            "rights": (
                {"can_read_messages": True, "can_reply": True}
                if rights is None else rights
            ),
        },
    })


def _customer_update(
    update_id,
    *,
    connection_id,
    chat_id,
    customer_id,
    message_id=None,
    is_from_business=False,
):
    return parse_telegram_update({
        "update_id": update_id,
        "business_message": {
            "business_connection_id": connection_id,
            "message_id": update_id if message_id is None else message_id,
            "date": 1_800_000_010 + update_id,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": customer_id, "is_bot": False},
            "is_from_business_account": is_from_business,
            "text": "پیام مشتری",
        },
    })


def make_actor(
    registry,
    *,
    connection_id="business-123",
    owner_id=123,
    chat_id=456,
    customer_id=456,
    connection_update_id=1,
    message_update_id=2,
    rights=None,
):
    processor = BusinessEventProcessor(registry, frozenset({owner_id}))
    connection_result = processor.process(
        _connection_update(
            connection_update_id,
            connection_id=connection_id,
            owner_id=owner_id,
            rights=rights,
        ),
        now=1_800_000_100 + connection_update_id,
    )
    assert connection_result.status == "connection_enabled"
    message = _customer_update(
        message_update_id,
        connection_id=connection_id,
        chat_id=chat_id,
        customer_id=customer_id,
    )
    result = processor.process(message, now=1_800_000_200 + message_update_id)
    assert result.status == "customer_context_resolved"
    return result.actor


def make_store(tmp_path, *, key=None, retention=3600, max_sessions=100, max_messages=20):
    registry = BusinessConnectionStore(tmp_path / "registry" / "connections.json")
    actor = make_actor(registry)
    session_store = BusinessConversationStore(
        tmp_path / "business-private" / "sessions.json",
        b"k" * 32 if key is None else key,
        registry,
        retention_seconds=retention,
        max_sessions=max_sessions,
        max_messages_per_session=max_messages,
    )
    return registry, actor, session_store


def test_load_or_create_business_session_key_uses_protected_credential_store():
    credentials = MemoryCredentialStore()

    first = load_or_create_business_session_key(credentials)
    second = load_or_create_business_session_key(credentials)

    assert len(first) == 32
    assert second == first
    assert set(credentials.values[BUSINESS_SESSION_KEY_CREDENTIAL_ID]) == {
        "purpose", "version", "key"
    }


def test_load_or_create_business_session_key_fails_closed_when_store_unavailable():
    with pytest.raises(BusinessSessionError, match="Protected Business session key"):
        load_or_create_business_session_key(FailingCredentialStore())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"purpose": "wrong", "version": 1, "key": base64.b64encode(b"k" * 32).decode()},
        {"purpose": "telegram-business-customer-sessions", "version": True, "key": base64.b64encode(b"k" * 32).decode()},
        {"purpose": "telegram-business-customer-sessions", "version": 1, "key": "not-base64"},
        {"purpose": "telegram-business-customer-sessions", "version": 1, "key": base64.b64encode(b"short").decode()},
    ],
)
def test_load_or_create_business_session_key_rejects_invalid_stored_material(payload):
    credentials = MemoryCredentialStore()
    credentials.values[BUSINESS_SESSION_KEY_CREDENTIAL_ID] = payload

    with pytest.raises(BusinessSessionError):
        load_or_create_business_session_key(credentials)


def test_business_session_round_trip_is_encrypted_and_private_on_posix(tmp_path):
    _registry, actor, store = make_store(tmp_path)
    private_message = "CUSTOMER_PRIVATE_SENTENCE_DO_NOT_STORE_IN_PLAINTEXT"

    assert store.append_message(
        actor,
        role="customer",
        message_id=actor.message_id,
        content=private_message,
        now=1_800_000_300,
    )
    history = store.read_history(actor, now=1_800_000_301)

    assert len(history) == 1
    assert history[0].role == "customer"
    assert history[0].content == private_message
    raw = store._path.read_bytes()
    assert private_message.encode() not in raw
    assert actor.connection_id.encode() not in raw
    assert str(actor.customer_user_id).encode() not in raw
    if os.name == "posix":
        assert stat.S_IMODE(store._path.stat().st_mode) == 0o600
        assert stat.S_IMODE(store._path.parent.stat().st_mode) == 0o700


def test_business_session_store_loads_ciphertext_across_instances(tmp_path):
    registry, actor, store = make_store(tmp_path)
    store.append_message(
        actor,
        role="customer",
        message_id=actor.message_id,
        content="متن خصوصی",
        now=1_800_000_300,
    )
    restored = BusinessConversationStore(
        store._path,
        b"k" * 32,
        registry,
    )

    assert restored.load() == 1
    history = restored.read_history(actor, now=1_800_000_301)
    assert [item.content for item in history] == ["متن خصوصی"]


def test_business_session_store_keeps_connections_and_customer_chats_isolated(tmp_path):
    registry = BusinessConnectionStore(tmp_path / "registry" / "connections.json")
    actor_a = make_actor(
        registry,
        connection_id="business-a",
        owner_id=123,
        chat_id=456,
        customer_id=456,
        connection_update_id=1,
        message_update_id=2,
    )
    actor_b = make_actor(
        registry,
        connection_id="business-b",
        owner_id=123,
        chat_id=456,
        customer_id=456,
        connection_update_id=3,
        message_update_id=4,
    )
    actor_c = make_actor(
        registry,
        connection_id="business-a",
        owner_id=123,
        chat_id=789,
        customer_id=789,
        connection_update_id=5,
        message_update_id=6,
    )
    store = BusinessConversationStore(
        tmp_path / "business-private" / "sessions.json",
        b"k" * 32,
        registry,
    )

    store.append_message(actor_a, role="customer", message_id=2, content="A", now=100)
    store.append_message(actor_b, role="customer", message_id=4, content="B", now=101)
    store.append_message(actor_c, role="customer", message_id=6, content="C", now=102)

    assert [item.content for item in store.read_history(actor_a, now=103)] == ["A"]
    assert [item.content for item in store.read_history(actor_b, now=103)] == ["B"]
    assert [item.content for item in store.read_history(actor_c, now=103)] == ["C"]


def test_business_session_store_rejects_same_session_with_different_customer_identity(tmp_path):
    registry, actor, store = make_store(tmp_path)
    store.append_message(
        actor,
        role="customer",
        message_id=actor.message_id,
        content="محرمانه",
        now=100,
    )
    forged_actor = type(actor)(
        contract_version=actor.contract_version,
        actor_type=actor.actor_type,
        connection_id=actor.connection_id,
        business_user_id=actor.business_user_id,
        customer_user_id=999,
        chat_id=actor.chat_id,
        message_id=actor.message_id,
        can_reply=actor.can_reply,
        capabilities=(),
    )

    with pytest.raises(BusinessSessionAuthorizationError):
        store.read_history(forged_actor, now=101)


def test_business_session_duplicate_message_is_idempotent(tmp_path):
    _registry, actor, store = make_store(tmp_path)
    assert store.append_message(
        actor, role="customer", message_id=77, content="same", now=100
    )
    assert not store.append_message(
        actor, role="customer", message_id=77, content="different", now=101
    )
    assert [item.content for item in store.read_history(actor, now=102)] == ["same"]


def test_business_session_history_is_bounded_by_message_count(tmp_path):
    _registry, actor, store = make_store(tmp_path, max_messages=2)
    for message_id in (1, 2, 3):
        store.append_message(
            actor,
            role="customer",
            message_id=message_id,
            content=f"message-{message_id}",
            now=100 + message_id,
        )

    assert [item.content for item in store.read_history(actor, now=104)] == [
        "message-2",
        "message-3",
    ]


def test_assistant_history_requires_current_can_reply_permission(tmp_path):
    registry = BusinessConnectionStore(tmp_path / "registry" / "connections.json")
    actor = make_actor(
        registry,
        rights={"can_read_messages": True},
    )
    store = BusinessConversationStore(
        tmp_path / "business-private" / "sessions.json",
        b"k" * 32,
        registry,
    )

    with pytest.raises(BusinessSessionAuthorizationError):
        store.append_message(
            actor,
            role="assistant",
            message_id=900,
            content="پاسخ",
            now=100,
        )


def test_business_session_revalidates_current_read_and_reply_rights(tmp_path):
    registry = BusinessConnectionStore(tmp_path / "registry" / "connections.json")
    actor = make_actor(registry)
    store = BusinessConversationStore(
        tmp_path / "business-private" / "sessions.json",
        b"k" * 32,
        registry,
    )
    store.append_message(
        actor, role="customer", message_id=actor.message_id, content="hello", now=100
    )

    registry.update_from_event(
        {
            "contract_version": "telegram-business-event.v1",
            "event_type": "business_connection",
            "connection_id": actor.connection_id,
            "business_user_id": actor.business_user_id,
            "user_chat_id": actor.business_user_id,
            "connected_at": 1_800_000_000,
            "is_enabled": True,
            "rights": {"can_read_messages": True},
        },
        owner_ids=frozenset({actor.business_user_id}),
        update_id=3,
        now=101,
    )
    with pytest.raises(BusinessSessionAuthorizationError):
        store.append_message(
            actor, role="assistant", message_id=901, content="old reply", now=102
        )
    assert store.read_history(actor, now=103)[0].content == "hello"

    registry.update_from_event(
        {
            "contract_version": "telegram-business-event.v1",
            "event_type": "business_connection",
            "connection_id": actor.connection_id,
            "business_user_id": actor.business_user_id,
            "user_chat_id": actor.business_user_id,
            "connected_at": 1_800_000_000,
            "is_enabled": True,
            "rights": {"can_reply": True},
        },
        owner_ids=frozenset({actor.business_user_id}),
        update_id=4,
        now=104,
    )
    with pytest.raises(BusinessSessionAuthorizationError):
        store.read_history(actor, now=105)


def test_business_session_read_stops_after_connection_is_disabled_but_delete_remains_available(tmp_path):
    registry, actor, store = make_store(tmp_path)
    store.append_message(
        actor, role="customer", message_id=actor.message_id, content="private", now=100
    )
    registry.update_from_event(
        {
            "contract_version": "telegram-business-event.v1",
            "event_type": "business_connection",
            "connection_id": actor.connection_id,
            "business_user_id": actor.business_user_id,
            "user_chat_id": actor.business_user_id,
            "connected_at": 1_800_000_000,
            "is_enabled": False,
            "rights": {},
        },
        owner_ids=frozenset({actor.business_user_id}),
        update_id=3,
        now=101,
    )

    with pytest.raises(BusinessSessionAuthorizationError):
        store.read_history(actor, now=102)
    assert store.delete_session(actor)
    assert store.load() == 0


def test_business_session_expiry_removes_stale_history(tmp_path):
    _registry, actor, store = make_store(tmp_path, retention=60)
    store.append_message(
        actor, role="customer", message_id=actor.message_id, content="expires", now=100
    )

    assert store.read_history(actor, now=160)
    assert store.read_history(actor, now=161) == ()
    assert store.load() == 0


def test_business_session_prune_expired_removes_only_old_sessions(tmp_path):
    registry = BusinessConnectionStore(tmp_path / "registry" / "connections.json")
    actor_a = make_actor(
        registry,
        connection_id="business-a",
        chat_id=456,
        customer_id=456,
        connection_update_id=1,
        message_update_id=2,
    )
    actor_b = make_actor(
        registry,
        connection_id="business-b",
        chat_id=789,
        customer_id=789,
        connection_update_id=3,
        message_update_id=4,
    )
    store = BusinessConversationStore(
        tmp_path / "business-private" / "sessions.json",
        b"k" * 32,
        registry,
        retention_seconds=60,
    )
    store.append_message(actor_a, role="customer", message_id=2, content="old", now=100)
    store.append_message(actor_b, role="customer", message_id=4, content="new", now=150)

    assert store.prune_expired(now=161) == 1
    assert store.read_history(actor_a, now=161) == ()
    assert store.read_history(actor_b, now=161)[0].content == "new"


def test_business_session_delete_rejects_a_different_customer_identity(tmp_path):
    _registry, actor, store = make_store(tmp_path)
    store.append_message(
        actor,
        role="customer",
        message_id=actor.message_id,
        content="private",
        now=100,
    )
    forged_actor = type(actor)(
        contract_version=actor.contract_version,
        actor_type=actor.actor_type,
        connection_id=actor.connection_id,
        business_user_id=actor.business_user_id,
        customer_user_id=999,
        chat_id=actor.chat_id,
        message_id=actor.message_id,
        can_reply=actor.can_reply,
        capabilities=(),
    )

    with pytest.raises(BusinessSessionAuthorizationError):
        store.delete_session(forged_actor)
    assert store.read_history(actor, now=101)[0].content == "private"


def test_business_session_delete_is_idempotent(tmp_path):
    _registry, actor, store = make_store(tmp_path)
    assert not store.delete_session(actor)
    store.append_message(
        actor, role="customer", message_id=actor.message_id, content="delete", now=100
    )
    assert store.delete_session(actor)
    assert not store.delete_session(actor)


def test_business_session_capacity_fails_closed_without_eviction(tmp_path):
    registry = BusinessConnectionStore(tmp_path / "registry" / "connections.json")
    actor_a = make_actor(
        registry,
        chat_id=456,
        customer_id=456,
        connection_update_id=1,
        message_update_id=2,
    )
    actor_b = make_actor(
        registry,
        chat_id=789,
        customer_id=789,
        connection_update_id=3,
        message_update_id=4,
    )
    store = BusinessConversationStore(
        tmp_path / "business-private" / "sessions.json",
        b"k" * 32,
        registry,
        max_sessions=1,
    )
    store.append_message(actor_a, role="customer", message_id=2, content="first", now=100)

    with pytest.raises(BusinessSessionError, match="count limit"):
        store.append_message(actor_b, role="customer", message_id=4, content="second", now=101)
    assert store.read_history(actor_a, now=102)[0].content == "first"


def test_business_session_rejects_wrong_keys_and_tampered_ciphertext(tmp_path):
    _registry, actor, store = make_store(tmp_path)
    store.append_message(
        actor, role="customer", message_id=actor.message_id, content="secret", now=100
    )
    wrong_key = BusinessConversationStore(
        store._path,
        b"x" * 32,
        store._connection_store,
    )
    with pytest.raises(BusinessSessionError):
        wrong_key.read_history(actor, now=101)

    envelope = json.loads(store._path.read_text(encoding="utf-8"))
    session_key, record = next(iter(envelope["sessions"].items()))
    ciphertext = bytearray(base64.b64decode(record["ciphertext"]))
    ciphertext[-1] ^= 1
    record["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
    store._path.write_text(json.dumps(envelope), encoding="utf-8")
    if os.name == "posix":
        store._path.chmod(0o600)
    with pytest.raises(BusinessSessionError):
        store.read_history(actor, now=102)


def test_business_session_store_rejects_duplicate_json_keys_and_symlinks(tmp_path):
    path = tmp_path / "business-private" / "sessions.json"
    path.parent.mkdir(mode=0o700)
    path.write_text('{"version":1,"version":1,"sessions":{}}', encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    _registry = BusinessConnectionStore(tmp_path / "registry" / "connections.json")
    with pytest.raises(BusinessSessionError):
        BusinessConversationStore(path, b"k" * 32, _registry).load()

    if hasattr(os, "O_NOFOLLOW"):
        target = tmp_path / "target.json"
        target.write_text('{"version":1,"sessions":{}}', encoding="utf-8")
        link = tmp_path / "business-private" / "link.json"
        link.symlink_to(target)
        with pytest.raises(BusinessSessionError):
            BusinessConversationStore(link, b"k" * 32, _registry).load()
