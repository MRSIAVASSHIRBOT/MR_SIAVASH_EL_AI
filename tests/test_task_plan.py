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


def test_task_plan_store_defaults_to_sequential_dependencies_and_persists_dag(tmp_path):
    path = tmp_path / "task-plans.json"
    store = TaskPlanStore(path)
    sequential = store.create(123, "demo.echo", ["one", "two", "three"], now=100)
    assert [step.depends_on for step in sequential.steps] == [(), (0,), (1,)]

    dag = store.create(
        123,
        "demo.echo",
        ["collect", "inspect", "publish"],
        dependencies=[[], [0], [0, 1]],
        now=101,
    )
    assert [step.depends_on for step in dag.steps] == [(), (0,), (0, 1)]
    assert store.mark_submitted(dag.plan_id, 123, 0, TASK_A) is True
    with pytest.raises(TaskPlanError, match="dependencies must be submitted"):
        store.mark_submitted(dag.plan_id, 123, 2, TASK_B)
    assert store.mark_blocked(
        dag.plan_id, 123, 2, blocked_by=[1], reason="dependency_failed"
    ) is True

    restored = TaskPlanStore(path)
    loaded = restored.get_for_user(dag.plan_id, 123, now=102)
    assert loaded is not None
    assert loaded.steps[2].blocked_by == (1,)
    assert loaded.steps[2].blocked_reason == "dependency_failed"
    assert loaded.steps[2].task_id is None
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    assert payload["plans"][1]["steps"][2]["depends_on"] == [0, 1]


@pytest.mark.parametrize(
    "dependencies",
    [
        [[0], [], []],
        [[1], [], []],
        [[], [0, 0], []],
        [[], [True], []],
        [[], [0], [3]],
        [[], [0]],
    ],
)
def test_task_plan_store_rejects_invalid_dependency_graphs(tmp_path, dependencies):
    store = TaskPlanStore(tmp_path / "task-plans.json")
    with pytest.raises(TaskPlanError):
        store.create(
            123,
            "demo.echo",
            ["one", "two", "three"],
            dependencies=dependencies,
            now=100,
        )


def test_task_plan_store_migrates_v1_plans_to_v2_without_losing_checkpoints(tmp_path):
    path = tmp_path / "task-plans.json"
    path.write_text(json.dumps({
        "version": 1,
        "plans": [{
            "plan_id": TASK_A,
            "telegram_user_id": 123,
            "task_type": "demo.echo",
            "created_at": 100,
            "steps": [
                {"description": "one", "task_id": TASK_B},
                {"description": "two", "task_id": None},
            ],
        }],
    }), encoding="utf-8")

    store = TaskPlanStore(path)
    loaded = store.get_for_user(TASK_A, 123, now=101)
    assert loaded is not None
    assert loaded.steps[0].task_id == TASK_B
    assert loaded.steps[0].depends_on == ()
    assert loaded.steps[1].depends_on == (0,)
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["version"] == 2
    assert migrated["plans"][0]["steps"][1]["depends_on"] == [0]


def test_task_plan_store_rejects_blocked_state_that_conflicts_with_submission(tmp_path):
    path = tmp_path / "task-plans.json"
    store = TaskPlanStore(path)
    plan = store.create(123, "demo.echo", ["one", "two"], now=100)
    store.mark_submitted(plan.plan_id, 123, 0, TASK_A)
    with pytest.raises(TaskPlanError, match="submitted task-plan step"):
        store.mark_blocked(plan.plan_id, 123, 0, reason="hub_policy_denied")
