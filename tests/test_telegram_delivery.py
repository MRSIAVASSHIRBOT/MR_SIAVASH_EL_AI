import asyncio
import os
import stat

import pytest

from el_ai.telegram.api import TelegramAPIError
from el_ai.telegram.delivery import DeliveryStore, DeliveryStoreError, send_update_response


def _store(tmp_path):
    store = DeliveryStore(tmp_path / "telegram-delivery.sqlite3")
    store.initialize()
    return store


def test_delivery_store_creates_private_database_without_response_text(tmp_path):
    store = _store(tmp_path)
    store.prepare_update(10, 123, ["secret response text"])
    assert b"secret response text" not in (tmp_path / "telegram-delivery.sqlite3").read_bytes()
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "telegram-delivery.sqlite3").stat().st_mode) == 0o600


def test_delivery_store_persists_and_finishes_each_chunk(tmp_path):
    store = _store(tmp_path)
    assert store.prepare_update(10, 123, ["one", "two"]) == "pending"
    assert store.begin_chunk(10, 0) is True
    store.mark_sent(10, 0, 101)
    assert store.batch_state(10) == "in_progress"
    assert store.chunk_states(10) == (("sent", 101), ("pending", None))
    assert store.begin_chunk(10, 1) is True
    store.mark_sent(10, 1, 102)
    assert store.batch_state(10) == "sent"
    assert store.chunk_states(10) == (("sent", 101), ("sent", 102))
    assert store.prepare_update(10, 123, ["one", "two"]) == "sent"


def test_delivery_store_never_reclaims_a_sending_chunk_after_restart(tmp_path):
    store = _store(tmp_path)
    store.prepare_update(11, 123, ["one"])
    assert store.begin_chunk(11, 0) is True
    restarted = DeliveryStore(tmp_path / "telegram-delivery.sqlite3")
    restarted.initialize()
    assert restarted.prepare_update(11, 123, ["one"]) == "quarantined"
    assert restarted.batch_state(11) == "quarantined"
    assert restarted.chunk_states(11) == (("unknown", None),)
    assert restarted.begin_chunk(11, 0) is False


def test_delivery_store_quarantines_changed_response_for_same_update(tmp_path):
    store = _store(tmp_path)
    store.prepare_update(12, 123, ["original"])
    assert store.prepare_update(12, 123, ["changed"]) == "quarantined"
    assert store.batch_state(12) == "quarantined"


def test_delivery_store_quarantines_unknown_outcome_and_never_retries(tmp_path):
    store = _store(tmp_path)
    store.prepare_update(13, 123, ["one"])
    assert store.begin_chunk(13, 0) is True
    store.mark_unknown(13, 0)
    assert store.batch_state(13) == "quarantined"
    assert store.prepare_update(13, 123, ["one"]) == "quarantined"
    assert store.begin_chunk(13, 0) is False


@pytest.mark.parametrize(
    ("update_id", "chat_id", "chunks"),
    [
        (True, 123, ["x"]),
        (-1, 123, ["x"]),
        (1, 0, ["x"]),
        (1, True, ["x"]),
        (1, 123, "not-a-list"),
        (1, 123, [object()]),
    ],
)
def test_delivery_store_rejects_invalid_input(tmp_path, update_id, chat_id, chunks):
    with pytest.raises(ValueError):
        _store(tmp_path).prepare_update(update_id, chat_id, chunks)


def test_delivery_store_rejects_symlink_when_supported(tmp_path):
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("Symlink protections are platform-dependent")
    target = tmp_path / "target.sqlite3"
    target.write_bytes(b"not a database")
    link = tmp_path / "delivery.sqlite3"
    link.symlink_to(target)
    with pytest.raises(DeliveryStoreError):
        DeliveryStore(link).initialize()


def test_delivery_store_rejects_uninitialized_access(tmp_path):
    with pytest.raises(DeliveryStoreError):
        DeliveryStore(tmp_path / "missing.sqlite3").prepare_update(1, 123, ["x"])


def test_delivery_store_empty_response_is_complete(tmp_path):
    assert _store(tmp_path).prepare_update(14, 123, []) == "sent"


def test_send_update_response_is_idempotent_after_confirmed_delivery(tmp_path):
    class FakeAPI:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))
            return {"message_id": len(self.sent)}

    store, api = _store(tmp_path), FakeAPI()
    first = asyncio.run(send_update_response(
        api, store, update_id=20, chat_id=123, response="hello"
    ))
    second = asyncio.run(send_update_response(
        api, store, update_id=20, chat_id=123, response="hello"
    ))
    assert first == "delivered"
    assert second == "already_delivered"
    assert api.sent == [(123, "hello")]


def test_send_update_response_quarantines_partial_chunk_delivery(tmp_path):
    class FakeAPI:
        def __init__(self):
            self.calls = 0

        async def send_message(self, chat_id, text):
            self.calls += 1
            if self.calls == 2:
                raise TelegramAPIError("ambiguous transport failure")
            return {"message_id": 700 + self.calls}

    store, api = _store(tmp_path), FakeAPI()
    response = "x" * 5000
    first = asyncio.run(send_update_response(
        api, store, update_id=21, chat_id=123, response=response
    ))
    second = asyncio.run(send_update_response(
        api, store, update_id=21, chat_id=123, response=response
    ))
    assert first == second == "quarantined"
    assert api.calls == 2
    assert store.batch_state(21) == "quarantined"
    assert store.chunk_states(21)[0] == ("sent", 701)
    assert store.chunk_states(21)[1][0] == "unknown"

def test_delivery_store_does_not_persist_raw_chat_id(tmp_path):
    store = _store(tmp_path)
    store.prepare_update(15, 987654321, ["safe"])
    import sqlite3
    with sqlite3.connect(tmp_path / "telegram-delivery.sqlite3") as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(delivery_batches)")}
    assert "chat_id_digest" in columns
    assert "chat_id" not in columns


def test_send_update_response_resumes_only_chunks_known_not_to_have_been_sent(tmp_path):
    from el_ai.telegram.api import split_telegram_message

    store = _store(tmp_path)
    response = "x" * 5000
    chunks = split_telegram_message(response)
    store.prepare_update(22, 123, chunks)
    assert store.begin_chunk(22, 0) is True
    store.mark_sent(22, 0, 800)

    class FakeAPI:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))
            return {"message_id": 801}

    api = FakeAPI()
    result = asyncio.run(send_update_response(
        api, store, update_id=22, chat_id=123, response=response
    ))
    assert result == "delivered"
    assert api.sent == [(123, chunks[1])]
    assert store.chunk_states(22) == (("sent", 800), ("sent", 801))


def test_send_update_response_quarantines_success_without_message_identifier(tmp_path):
    class FakeAPI:
        async def send_message(self, chat_id, text):
            return {"ok": True}

    store = _store(tmp_path)
    result = asyncio.run(send_update_response(
        FakeAPI(), store, update_id=23, chat_id=123, response="hello"
    ))
    assert result == "quarantined"
    assert store.batch_state(23) == "quarantined"
    assert store.chunk_states(23) == (("unknown", None),)
