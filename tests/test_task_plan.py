import copy
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
    assert payload["version"] == 4
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


def test_task_plan_store_migrates_v1_plans_to_v4_without_losing_checkpoints(tmp_path):
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
    assert migrated["version"] == 4
    assert migrated["plans"][0]["steps"][1]["depends_on"] == [0]


def test_task_plan_store_rejects_blocked_state_that_conflicts_with_submission(tmp_path):
    path = tmp_path / "task-plans.json"
    store = TaskPlanStore(path)
    plan = store.create(123, "demo.echo", ["one", "two"], now=100)
    store.mark_submitted(plan.plan_id, 123, 0, TASK_A)
    with pytest.raises(TaskPlanError, match="submitted task-plan step"):
        store.mark_blocked(plan.plan_id, 123, 0, reason="hub_policy_denied")



def test_task_plan_pause_state_is_persisted_owner_scoped_and_idempotent(tmp_path):
    path = tmp_path / "task-plans.json"
    store = TaskPlanStore(path)
    plan = store.create(123, "demo.echo", ["prepare", "publish"], now=100)

    assert store.set_paused(plan.plan_id, 123, True) is True
    assert store.set_paused(plan.plan_id, 123, True) is False

    restored = TaskPlanStore(path)
    loaded = restored.get_for_user(plan.plan_id, 123, now=101)
    assert loaded is not None
    assert loaded.paused is True
    assert restored.set_paused(plan.plan_id, 123, False) is True
    assert restored.get_for_user(plan.plan_id, 123, now=102).paused is False

    with pytest.raises(TaskPlanError, match="unavailable"):
        restored.set_paused(plan.plan_id, 456, True)
    with pytest.raises(ValueError, match="boolean"):
        restored.set_paused(plan.plan_id, 123, 1)


def test_task_plan_store_migrates_v2_to_v4_with_pause_disabled(tmp_path):
    path = tmp_path / "task-plans-v2.json"
    path.write_text(json.dumps({
        "version": 2,
        "plans": [{
            "plan_id": TASK_A,
            "telegram_user_id": 123,
            "task_type": "demo.echo",
            "created_at": 100,
            "steps": [
                {
                    "description": "one",
                    "task_id": TASK_B,
                    "depends_on": [],
                    "blocked_by": [],
                    "blocked_reason": None,
                },
                {
                    "description": "two",
                    "task_id": None,
                    "depends_on": [0],
                    "blocked_by": [],
                    "blocked_reason": None,
                },
            ],
        }],
    }), encoding="utf-8")

    store = TaskPlanStore(path)
    loaded = store.get_for_user(TASK_A, 123, now=101)
    assert loaded is not None
    assert loaded.paused is False
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["version"] == 4
    assert migrated["plans"][0]["paused"] is False


def test_task_plan_store_rejects_non_boolean_v3_pause_state(tmp_path):
    path = tmp_path / "task-plans-invalid-v3.json"
    path.write_text(json.dumps({
        "version": 3,
        "plans": [{
            "plan_id": TASK_A,
            "telegram_user_id": 123,
            "task_type": "demo.echo",
            "created_at": 100,
            "paused": 1,
            "steps": [
                {
                    "description": "one",
                    "task_id": None,
                    "depends_on": [],
                    "blocked_by": [],
                    "blocked_reason": None,
                },
                {
                    "description": "two",
                    "task_id": None,
                    "depends_on": [0],
                    "blocked_by": [],
                    "blocked_reason": None,
                },
            ],
        }],
    }), encoding="utf-8")

    with pytest.raises(TaskPlanError, match="pause state"):
        TaskPlanStore(path).load()


def test_task_plan_store_pause_all_is_atomic_idempotent_and_owner_scoped(tmp_path):
    path = tmp_path / "task-plans.json"
    store = TaskPlanStore(path)
    first = store.create(123, "demo.echo", ["first", "publish"], now=100)
    second = store.create(123, "demo.echo", ["prepare", "ship"], now=101)
    foreign = store.create(456, "demo.echo", ["other", "owner"], now=102)

    assert store.set_all_paused(123, True) == 2
    assert store.set_all_paused(123, True) == 0
    assert store.get_for_user(first.plan_id, 123, now=103).paused is True
    assert store.get_for_user(second.plan_id, 123, now=103).paused is True
    assert store.get_for_user(foreign.plan_id, 456, now=103).paused is False

    assert store.set_all_paused(123, False) == 2
    assert store.set_all_paused(123, False) == 0
    assert store.get_for_user(first.plan_id, 123, now=104).paused is False
    assert store.get_for_user(foreign.plan_id, 456, now=104).paused is False


def test_task_plan_store_pause_all_rejects_invalid_owner_and_state(tmp_path):
    store = TaskPlanStore(tmp_path / "task-plans.json")
    with pytest.raises(ValueError, match="positive integer"):
        store.set_all_paused(True, True)
    with pytest.raises(ValueError, match="boolean"):
        store.set_all_paused(123, 1)

def _telegram_agent_plan_contract():
    return {
        "contract_version": "telegram_agent_plan.v1",
        "planning_status": "ready_for_review",
        "evaluation_scope": "planning_only",
        "goal_satisfaction": "not_evaluated",
        "goal": "Prepare a reviewable proposal",
        "intent": "Plan only",
        "scope": "",
        "assumptions": [],
        "constraints": ["No tools or execution in preview"],
        "dependencies": [],
        "required_capabilities": ["file.read"],
        "capability_availability": "not_assessed",
        "authorization_checked": False,
        "estimated_complexity": "moderate",
        "missing_information": [],
        "clarification_questions": [],
        "research_required": False,
        "research_questions": [],
        "risk_class": "low",
        "risk_class_is_authoritative": False,
        "acceptance_criteria": [
            {"criterion_id": "ac_001", "statement": "The proposal includes bounded inputs"},
            {"criterion_id": "ac_002", "statement": "Every step has a verification method"},
        ],
        "verification_steps": [
            "Inspect the input schema",
            "Review step-to-criterion coverage",
        ],
        "steps": [
            {
                "step_id": "prepare",
                "description": "Prepare bounded inputs",
                "depends_on": [],
                "required_capabilities": ["file.read"],
                "verifies_criteria": ["ac_001"],
                "verification": "Inspect the input schema",
            },
            {
                "step_id": "review",
                "description": "Review plan coverage",
                "depends_on": ["prepare"],
                "required_capabilities": [],
                "verifies_criteria": ["ac_002"],
                "verification": "Review step-to-criterion coverage",
            },
        ],
        "execution_ready": False,
        "execution_started": False,
        "tools_executed": False,
        "tasks_created": 0,
    }


def test_task_plan_store_persists_and_reloads_versioned_acceptance_contract(tmp_path):
    path = tmp_path / "task-plans-v4.json"
    store = TaskPlanStore(path)
    contract = _telegram_agent_plan_contract()

    plan = store.create_from_contract(123, "demo.echo", contract, now=100)

    assert plan.contract_version == "telegram_agent_plan.v1"
    assert plan.evaluation_scope == "planning_only"
    assert plan.planning_status == "ready_for_review"
    assert plan.goal_satisfaction == "not_evaluated"
    assert plan.capability_availability == "not_assessed"
    assert plan.authorization_checked is False
    assert [item.criterion_id for item in plan.acceptance_criteria] == ["ac_001", "ac_002"]
    assert [step.step_id for step in plan.steps] == ["prepare", "review"]
    assert [step.depends_on for step in plan.steps] == [(), (0,)]
    assert [step.verifies_criteria for step in plan.steps] == [("ac_001",), ("ac_002",)]
    assert plan.submitted_count == 0

    # A normal owner-scoped state transition must not discard the contract metadata.
    assert store.mark_submitted(plan.plan_id, 123, 0, TASK_A) is True
    assert store.set_paused(plan.plan_id, 123, True) is True
    restored = TaskPlanStore(path).get_for_user(plan.plan_id, 123, now=101)
    assert restored is not None
    assert restored.paused is True
    assert restored.steps[0].task_id == TASK_A
    assert restored.steps[0].step_id == "prepare"
    assert restored.steps[0].verifies_criteria == ("ac_001",)
    assert restored.acceptance_criteria == plan.acceptance_criteria
    assert restored.verification_steps == plan.verification_steps
    assert restored.goal_satisfaction == "not_evaluated"
    assert "Prepare a reviewable proposal" not in path.read_text(encoding="utf-8")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 4
    assert payload["plans"][0]["contract_version"] == "telegram_agent_plan.v1"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: item.update(contract_version="telegram_agent_plan.v2"),
        lambda item: item.update(goal_satisfaction="succeeded"),
        lambda item: item.update(execution_started=True),
        lambda item: item.update(tools_executed=True),
        lambda item: item.update(tasks_created=1),
        lambda item: item.update(authorization_checked=True),
        lambda item: item.update(capability_availability="available"),
        lambda item: item.update(
            planning_status="ready_for_review",
            missing_information=["Target environment is unknown"],
            clarification_questions=["Which target environment should be used?"],
        ),
        lambda item: item.update(planning_status="needs_research"),
        lambda item: item.update(missing_information=["Target environment is unknown"]),
        lambda item: item.update(research_required=True),
        lambda item: item.update(research_questions=["Review target requirements"]),

        lambda item: item["acceptance_criteria"].append(item["acceptance_criteria"][0]),
        lambda item: item["steps"][1].update(step_id="prepare"),
        lambda item: item["steps"][1].update(depends_on=["future-step"]),
        lambda item: item["steps"][1].update(verifies_criteria=["ac_999"]),
        lambda item: item["steps"][1].update(verifies_criteria=["ac_001"]),
    ],
)
def test_task_plan_store_rejects_unsafe_or_inconsistent_contracts(tmp_path, mutate):
    contract = copy.deepcopy(_telegram_agent_plan_contract())
    mutate(contract)
    store = TaskPlanStore(tmp_path / "task-plans-invalid.json")
    with pytest.raises(TaskPlanError):
        store.create_from_contract(123, "demo.echo", contract, now=100)
    assert store.list_for_user(123, now=100) == ()


def test_task_plan_store_rejects_contract_when_legacy_steps_do_not_match(tmp_path):
    contract = _telegram_agent_plan_contract()
    store = TaskPlanStore(tmp_path / "task-plans-mismatch.json")

    with pytest.raises(TaskPlanError, match="do not match"):
        store.create(
            123,
            "demo.echo",
            ["Different step", "Review plan coverage"],
            now=100,
            plan_contract=contract,
        )

    assert store.list_for_user(123, now=100) == ()


def test_task_plan_store_migrates_v3_to_v4_without_losing_pause_or_checkpoint(tmp_path):
    path = tmp_path / "task-plans-v3.json"
    path.write_text(json.dumps({
        "version": 3,
        "plans": [{
            "plan_id": TASK_A,
            "telegram_user_id": 123,
            "task_type": "demo.echo",
            "created_at": 100,
            "paused": True,
            "steps": [
                {
                    "description": "one",
                    "task_id": TASK_B,
                    "depends_on": [],
                    "blocked_by": [],
                    "blocked_reason": None,
                },
                {
                    "description": "two",
                    "task_id": None,
                    "depends_on": [0],
                    "blocked_by": [],
                    "blocked_reason": None,
                },
            ],
        }],
    }), encoding="utf-8")

    plan = TaskPlanStore(path).get_for_user(TASK_A, 123, now=101)
    assert plan is not None
    assert plan.paused is True
    assert plan.steps[0].task_id == TASK_B
    assert plan.steps[0].depends_on == ()
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["version"] == 4
    assert migrated["plans"][0]["contract_version"] is None
    assert migrated["plans"][0]["goal_satisfaction"] == "not_evaluated"
    assert migrated["plans"][0]["authorization_checked"] is False


def test_task_plan_store_rejects_tampered_v4_contract_state(tmp_path):
    path = tmp_path / "task-plans-tampered.json"
    store = TaskPlanStore(path)
    store.create_from_contract(
        123, "demo.echo", _telegram_agent_plan_contract(), now=100
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["plans"][0]["steps"][1]["verifies_criteria"] = ["ac_999"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TaskPlanError, match="unknown|mapping|cover"):
        TaskPlanStore(path).load()


def test_task_plan_store_rejects_submission_before_clarification_or_research(tmp_path):
    for status in ("needs_clarification", "needs_research"):
        store = TaskPlanStore(tmp_path / f"{status}.json")
        contract = _telegram_agent_plan_contract()
        contract["planning_status"] = status
        if status == "needs_research":
            contract["missing_information"] = []
            contract["clarification_questions"] = []
            contract["research_required"] = True
            contract["research_questions"] = ["Review the target environment requirements"]
        else:
            contract["missing_information"] = ["The target environment is unknown"]
            contract["clarification_questions"] = ["Which target environment should be used?"]
        plan = store.create_from_contract(123, "demo.echo", contract, now=100)

        with pytest.raises(TaskPlanError, match="planning is ready"):
            store.mark_submitted(plan.plan_id, 123, 0, TASK_A)

        unchanged = store.get_for_user(plan.plan_id, 123, now=101)
        assert unchanged is not None
        assert unchanged.submitted_count == 0
