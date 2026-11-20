import json
import os
import stat

import pytest

from el_ai.telegram.task_plan import (
    MAX_TASK_PLAN_AGE_SECONDS,
    MAX_TASK_PLANS,
    TaskPlanError,
    TaskPlanStore,
)


TASK_A = "12345678-1234-4234-8234-123456789abc"
TASK_B = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def test_task_plan_store_persists_owner_scoped_steps_and_submission_checkpoints(tmp_path):
    path = tmp_path / "task-plans.json"
    store = TaskPlanStore(path)
    assert store.load() == ()

    plan = store.create(123, "demo.echo", ["prepare", "validate"], now=100)
    assert plan.submitted_count == 0
    assert store.mark_submitted(plan.plan_id, 123, 0, TASK_A) is True
    assert store.mark_submitted(plan.plan_id, 123, 0, TASK_A) is False

    restored = TaskPlanStore(path)
    loaded = restored.get_for_user(plan.plan_id, 123, now=101)
    assert loaded is not None
    assert loaded.steps[0].task_id == TASK_A
    assert loaded.steps[1].task_id is None
    assert loaded.submitted_count == 1
    assert restored.get_for_user(plan.plan_id, 456, now=101) is None
    assert len(restored.list_for_user(123, now=101)) == 1
    assert restored.list_for_user(456, now=101) == ()

    with pytest.raises(TaskPlanError, match="different task"):
        restored.mark_submitted(plan.plan_id, 123, 0, TASK_B)


def test_task_plan_store_validates_task_types_step_counts_and_descriptions(tmp_path):
    store = TaskPlanStore(tmp_path / "task-plans.json")
    with pytest.raises(ValueError):
        store.create(123, "invalid type", ["one", "two"], now=100)
    with pytest.raises(TaskPlanError, match="2–8"):
        store.create(123, "demo.echo", ["only one"], now=100)
    with pytest.raises(TaskPlanError):
        store.create(123, "demo.echo", ["one", ""], now=100)
    with pytest.raises(TaskPlanError):
        store.create(123, "demo.echo", ["x" * 401, "two"], now=100)
    with pytest.raises(ValueError):
        store.create(True, "demo.echo", ["one", "two"], now=100)


def test_task_plan_store_expires_old_drafts_and_bounds_collection(tmp_path):
    store = TaskPlanStore(tmp_path / "task-plans.json")
    first = store.create(123, "demo.echo", ["one", "two"], now=100)
    assert store.get_for_user(
        first.plan_id, 123, now=100 + MAX_TASK_PLAN_AGE_SECONDS + 1
    ) is None

    for index in range(MAX_TASK_PLANS):
        store.create(123, "demo.echo", [f"one-{index}", f"two-{index}"], now=200 + index)
    with pytest.raises(TaskPlanError, match="limit"):
        store.create(123, "demo.echo", ["extra one", "extra two"], now=300)


def test_task_plan_store_rejects_corrupt_and_duplicate_state(tmp_path):
    path = tmp_path / "task-plans.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(TaskPlanError):
        TaskPlanStore(path).load()

    path.write_text(json.dumps({"version": 1.0, "plans": []}), encoding="utf-8")
    with pytest.raises(TaskPlanError):
        TaskPlanStore(path).load()

    path.write_text(json.dumps({
        "version": 1,
        "plans": [
            {
                "plan_id": TASK_A,
                "telegram_user_id": 123,
                "task_type": "demo.echo",
                "created_at": 100,
                "steps": [
                    {"description": "one", "task_id": None},
                    {"description": "two", "task_id": None},
                ],
            },
            {
                "plan_id": TASK_A,
                "telegram_user_id": 123,
                "task_type": "demo.echo",
                "created_at": 101,
                "steps": [
                    {"description": "one", "task_id": None},
                    {"description": "two", "task_id": None},
                ],
            },
        ],
    }), encoding="utf-8")
    with pytest.raises(TaskPlanError, match="duplicated"):
        TaskPlanStore(path).load()


def test_task_plan_store_wraps_invalid_persisted_fields_as_store_errors(tmp_path):
    path = tmp_path / "task-plans.json"
    path.write_text(json.dumps({
        "version": 1,
        "plans": [{
            "plan_id": TASK_A,
            "telegram_user_id": True,
            "task_type": "demo.echo",
            "created_at": 100,
            "steps": [
                {"description": "one", "task_id": None},
                {"description": "two", "task_id": None},
            ],
        }],
    }), encoding="utf-8")
    with pytest.raises(TaskPlanError, match="entry is invalid"):
        TaskPlanStore(path).load()


def test_task_plan_store_uses_private_file_permissions_and_rejects_symlinks(tmp_path):
    path = tmp_path / "task-plans.json"
    TaskPlanStore(path).create(123, "demo.echo", ["one", "two"], now=100)
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("O_NOFOLLOW is not available on this platform")
    target = tmp_path / "target.json"
    target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    link = tmp_path / "linked-plans.json"
    link.symlink_to(target)
    with pytest.raises(TaskPlanError):
        TaskPlanStore(link).load()
