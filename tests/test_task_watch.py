import json

import pytest

from el_ai.telegram.task_watch import (
    MAX_TASK_WATCHES,
    TaskWatchError,
    TaskWatchStore,
)


TASK_A = "12345678-1234-4234-8234-123456789abc"
TASK_B = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


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


def test_task_watch_store_add_many_is_atomic_idempotent_and_owner_scoped(tmp_path):
    store = TaskWatchStore(tmp_path / "task-watches.json")
    store.load()
    store.add(TASK_A, 123, "running")

    added = store.add_many([(TASK_A, "running"), (TASK_B, "queued")], 123)

    assert added == (TASK_B,)
    assert {item.task_id for item in store.list_for_user(123)} == {TASK_A, TASK_B}
    assert store.list_for_user(456) == ()
    assert store.add_many([(TASK_B, "queued")], 123) == ()

    with pytest.raises(ValueError, match="active task statuses"):
        store.add_many([("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "succeeded")], 123)
    assert {item.task_id for item in store.list_for_user(123)} == {TASK_A, TASK_B}


def test_task_watch_store_add_many_checks_capacity_before_persisting(tmp_path):
    store = TaskWatchStore(tmp_path / "task-watches.json")
    store.load()
    for index in range(MAX_TASK_WATCHES - 1):
        task_id = f"12345678-1234-4234-8234-{index:012x}"
        store.add(task_id, 123, "queued")
    before = store.list()

    with pytest.raises(TaskWatchError, match="limit"):
        store.add_many([
            ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "running"),
            ("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "queued"),
        ], 123)

    assert store.list() == before


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



def test_task_watch_store_migrates_v1_state_and_persists_retry_backoff(tmp_path):
    path = tmp_path / "task-watches.json"
    path.write_text(json.dumps({
        "version": 1,
        "watches": [
            {"task_id": TASK_A, "telegram_user_id": 123, "last_status": "running"},
        ],
    }), encoding="utf-8")

    store = TaskWatchStore(path)
    migrated = store.load()[0]
    assert migrated.retry_count == 0
    assert migrated.next_attempt_at == 0
    assert store.defer(TASK_A, 123, 100) is True

    deferred = store.list()[0]
    assert deferred.retry_count == 1
    assert deferred.next_attempt_at == 130
    assert store.list_due(129) == ()
    assert store.list_due(130) == (deferred,)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["version"] == 2
    assert persisted["watches"][0]["retry_count"] == 1
    assert persisted["watches"][0]["next_attempt_at"] == 130


def test_task_watch_retry_backoff_is_exponential_bounded_and_reset_by_progress(tmp_path):
    store = TaskWatchStore(tmp_path / "task-watches.json")
    store.load()
    store.add(TASK_A, 123, "queued")

    expected_deadlines = (30, 160, 320, 560, 1040, 2000, 3800, 5600)
    now_values = (0, 100, 200, 320, 560, 1040, 2000, 3800)
    for now, expected in zip(now_values, expected_deadlines, strict=True):
        assert store.defer(TASK_A, 123, now) is True
        watch = store.list()[0]
        assert watch.next_attempt_at == expected

    assert store.list()[0].retry_count == 8
    assert store.update_status(TASK_A, 123, "running") is True
    watch = store.list()[0]
    assert watch.retry_count == 0
    assert watch.next_attempt_at == 0


def test_task_watch_store_rejects_invalid_retry_state_and_timestamps(tmp_path):
    path = tmp_path / "task-watches.json"
    path.write_text(json.dumps({
        "version": 2,
        "watches": [{
            "task_id": TASK_A,
            "telegram_user_id": 123,
            "last_status": "running",
            "retry_count": True,
            "next_attempt_at": 0,
        }],
    }), encoding="utf-8")
    with pytest.raises(TaskWatchError, match="retry state"):
        TaskWatchStore(path).load()

    store = TaskWatchStore(tmp_path / "other.json")
    with pytest.raises(ValueError):
        store.list_due(True)
    with pytest.raises(ValueError):
        store.defer(TASK_A, 123, -1)


def test_task_watch_status_updates_reject_stale_regressions_and_terminal_reopening(tmp_path):
    store = TaskWatchStore(tmp_path / "task-watches.json")
    store.load()
    store.add(TASK_A, 123, "queued")

    assert store.update_status(TASK_A, 123, "running") is True
    assert store.update_status(TASK_A, 123, "queued") is False
    assert store.list()[0].last_status == "running"

    assert store.update_status(TASK_A, 123, "succeeded") is True
    assert store.update_status(TASK_A, 123, "running") is False
    assert store.update_status(TASK_A, 123, "failed") is False
    assert store.list()[0].last_status == "succeeded"


def test_task_watch_status_updates_allow_approval_resume_and_terminal_failure(tmp_path):
    store = TaskWatchStore(tmp_path / "task-watches.json")
    store.load()
    store.add(TASK_A, 123, "queued")

    assert store.update_status(TASK_A, 123, "waiting_approval") is True
    assert store.update_status(TASK_A, 123, "running") is True
    assert store.update_status(TASK_A, 123, "failed") is True
    assert store.update_status(TASK_A, 123, "cancelled") is False
    assert store.list()[0].last_status == "failed"
