import json

import pytest

from el_ai.telegram.task_watch import (
    MAX_TASK_WATCHES,
    TaskWatchError,
    TaskWatchStore,
)


TASK_A = "12345678-1234-4234-8234-123456789abc"


def test_task_watch_store_persists_owner_scoped_status_checkpoints(tmp_path):
    path = tmp_path / "task-watches.json"
    store = TaskWatchStore(path)

    assert store.load() == ()
    assert store.add(TASK_A, 123, "queued") is True
    assert store.add(TASK_A, 123, "running") is False
    assert store.update_status(TASK_A, 123, "running") is True
    assert store.list_for_user(456) == ()

    restored = TaskWatchStore(path)
    assert restored.load()[0].task_id == TASK_A
    assert restored.list_for_user(123)[0].last_status == "running"
    assert restored.remove(TASK_A, 123) is True
    assert restored.list() == ()


def test_task_watch_store_rejects_terminal_or_invalid_watch_requests(tmp_path):
    store = TaskWatchStore(tmp_path / "task-watches.json")
    store.load()

    with pytest.raises(ValueError):
        store.add(TASK_A, 123, "succeeded")
    with pytest.raises(ValueError):
        store.add("not-a-uuid", 123, "running")
    with pytest.raises(ValueError):
        store.add(TASK_A, True, "running")
    with pytest.raises(ValueError):
        store.update_status(TASK_A, 123, "unknown")
    assert store.list() == ()


def test_task_watch_store_enforces_a_bounded_collection(tmp_path):
    store = TaskWatchStore(tmp_path / "task-watches.json")
    store.load()
    for index in range(MAX_TASK_WATCHES):
        task_id = f"12345678-1234-4234-8234-{index:012x}"
        store.add(task_id, 123, "queued")
    with pytest.raises(TaskWatchError, match="limit"):
        store.add("ffffffff-ffff-4fff-8fff-ffffffffffff", 123, "queued")
    assert len(store.list()) == MAX_TASK_WATCHES


def test_task_watch_store_rejects_corrupt_and_duplicate_state(tmp_path):
    path = tmp_path / "task-watches.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(TaskWatchError):
        TaskWatchStore(path).load()

    path.write_text(json.dumps({
        "version": 1,
        "watches": [
            {"task_id": TASK_A, "telegram_user_id": 123, "last_status": "running"},
            {"task_id": TASK_A, "telegram_user_id": 123, "last_status": "queued"},
        ],
    }), encoding="utf-8")
    with pytest.raises(TaskWatchError, match="duplicated"):
        TaskWatchStore(path).load()
