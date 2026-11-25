from __future__ import annotations

import time

from el_ai.integrations.hub import HubClientError
from el_ai.telegram.task_plan import TaskPlanStore
from el_ai.telegram.task_plan_orchestrator import advance_ready_plan_steps
from el_ai.telegram.task_watch import TaskWatchStore

TASK_A = "12345678-1234-4234-8234-123456789abc"
TASK_B = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TASK_C = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class FakeHub:
    def __init__(self):
        self.tasks = {}
        self.created = []
        self.fail_create = None
        self.on_create = None

    async def get_task(self, _owner_id, task_id):
        try:
            return self.tasks[task_id]
        except KeyError:
            raise HubClientError("task status is temporarily unavailable") from None

    async def create_task(self, _owner_id, task_type, description, *, idempotency_key):
        if self.fail_create is not None:
            raise self.fail_create
        existing = next(
            (item for item in self.created if item["idempotency_key"] == idempotency_key),
            None,
        )
        if existing is not None:
            return {"task_id": existing["task_id"], "status": self.tasks[existing["task_id"]]["status"]}
        task_id = (TASK_B, TASK_C)[len(self.created)]
        self.created.append({
            "task_id": task_id,
            "task_type": task_type,
            "description": description,
            "idempotency_key": idempotency_key,
        })
        self.tasks[task_id] = {"status": "queued"}
        if self.on_create is not None:
            self.on_create(task_id)
        return {"task_id": task_id, "status": "queued"}


async def test_plan_advances_only_after_all_dependencies_succeed(tmp_path):
    plans = TaskPlanStore(tmp_path / "plans.json")
    watches = TaskWatchStore(tmp_path / "watches.json")
    hub = FakeHub()
    plan = plans.create(
        123,
        "demo.echo",
        ["collect", "inspect", "publish"],
        dependencies=[[], [0], [0, 1]],
        now=int(time.time()),
    )
    plans.mark_submitted(plan.plan_id, 123, 0, TASK_A)
    hub.tasks[TASK_A] = {"status": "succeeded"}

    first = await advance_ready_plan_steps(hub, plans, watches, {123})
    assert [(event.kind, event.step_index, event.task_id) for event in first] == [
        ("submitted", 1, TASK_B)
    ]
    loaded = plans.get_for_user(plan.plan_id, 123, now=int(time.time()) + 1)
    assert loaded is not None
    assert loaded.steps[1].task_id == TASK_B
    assert loaded.steps[2].task_id is None
    assert watches.list_due(10**10)[0].task_id == TASK_B

    hub.tasks[TASK_B] = {"status": "running"}
    second = await advance_ready_plan_steps(hub, plans, watches, {123})
    assert second == ()
    assert len(hub.created) == 1

    hub.tasks[TASK_B] = {"status": "succeeded"}
    third = await advance_ready_plan_steps(hub, plans, watches, {123})
    assert [(event.kind, event.step_index, event.task_id) for event in third] == [
        ("submitted", 2, TASK_C)
    ]
    assert len(hub.created) == 2

    hub.tasks[TASK_C] = {"status": "queued"}
    assert await advance_ready_plan_steps(hub, plans, watches, {123}) == ()
    assert len(hub.created) == 2


async def test_failed_dependency_blocks_all_descendants_without_submitting_them(tmp_path):
    plans = TaskPlanStore(tmp_path / "plans.json")
    watches = TaskWatchStore(tmp_path / "watches.json")
    hub = FakeHub()
    plan = plans.create(
        123,
        "demo.echo",
        ["collect", "inspect", "publish"],
        dependencies=[[], [0], [1]],
        now=int(time.time()),
    )
    plans.mark_submitted(plan.plan_id, 123, 0, TASK_A)
    hub.tasks[TASK_A] = {"status": "failed"}

    events = await advance_ready_plan_steps(hub, plans, watches, {123})
    assert [(event.kind, event.step_index, event.reason) for event in events] == [
        ("blocked", 1, "dependency_failed"),
        ("blocked", 2, "dependency_blocked"),
    ]
    loaded = plans.get_for_user(plan.plan_id, 123, now=int(time.time()) + 1)
    assert loaded is not None
    assert loaded.steps[1].blocked_by == (0,)
    assert loaded.steps[2].blocked_by == (1,)
    assert not hub.created


async def test_unavailable_dependency_status_never_unlocks_a_step(tmp_path):
    plans = TaskPlanStore(tmp_path / "plans.json")
    watches = TaskWatchStore(tmp_path / "watches.json")
    hub = FakeHub()
    plan = plans.create(123, "demo.echo", ["first", "second"], now=int(time.time()))
    plans.mark_submitted(plan.plan_id, 123, 0, TASK_A)

    events = await advance_ready_plan_steps(hub, plans, watches, {123})
    assert events == ()
    assert not hub.created


async def test_hub_policy_denial_blocks_step_and_is_not_retried(tmp_path):
    plans = TaskPlanStore(tmp_path / "plans.json")
    watches = TaskWatchStore(tmp_path / "watches.json")
    hub = FakeHub()
    plan = plans.create(123, "demo.echo", ["first", "second"], now=int(time.time()))
    plans.mark_submitted(plan.plan_id, 123, 0, TASK_A)
    hub.tasks[TASK_A] = {"status": "succeeded"}
    hub.fail_create = HubClientError("permission denied", status_code=403)

    events = await advance_ready_plan_steps(hub, plans, watches, {123})
    assert [(event.kind, event.step_index, event.reason) for event in events] == [
        ("blocked", 1, "hub_policy_denied")
    ]
    loaded = plans.get_for_user(plan.plan_id, 123, now=int(time.time()) + 1)
    assert loaded is not None
    assert loaded.steps[1].blocked_reason == "hub_policy_denied"

    # The durable blocked checkpoint prevents a second API submission attempt.
    hub.fail_create = None
    assert await advance_ready_plan_steps(hub, plans, watches, {123}) == ()
    assert not hub.created



async def test_paused_plan_does_not_advance_until_explicitly_resumed(tmp_path):
    plans = TaskPlanStore(tmp_path / "plans.json")
    watches = TaskWatchStore(tmp_path / "watches.json")
    hub = FakeHub()
    plan = plans.create(123, "demo.echo", ["collect", "publish"], now=int(time.time()))
    plans.mark_submitted(plan.plan_id, 123, 0, TASK_A)
    hub.tasks[TASK_A] = {"status": "succeeded"}

    assert plans.set_paused(plan.plan_id, 123, True) is True
    assert await advance_ready_plan_steps(hub, plans, watches, {123}) == ()
    assert hub.created == []

    assert plans.set_paused(plan.plan_id, 123, False) is True
    events = await advance_ready_plan_steps(hub, plans, watches, {123})
    assert [(event.kind, event.step_index, event.task_id) for event in events] == [
        ("submitted", 1, TASK_B)
    ]
    assert len(hub.created) == 1


async def test_pause_during_advancement_stops_later_hub_dispatches(tmp_path):
    plans = TaskPlanStore(tmp_path / "plans.json")
    watches = TaskWatchStore(tmp_path / "watches.json")
    hub = FakeHub()
    plan = plans.create(
        123,
        "demo.echo",
        ["collect", "inspect", "publish"],
        dependencies=[[], [], []],
        now=int(time.time()),
    )
    plans.mark_submitted(plan.plan_id, 123, 0, TASK_A)
    hub.tasks[TASK_A] = {"status": "succeeded"}
    hub.on_create = lambda _task_id: plans.set_paused(plan.plan_id, 123, True)

    events = await advance_ready_plan_steps(hub, plans, watches, {123})

    assert [(event.kind, event.step_index, event.task_id) for event in events] == [
        ("submitted", 1, TASK_B)
    ]
    loaded = plans.get_for_user(plan.plan_id, 123, now=int(time.time()) + 1)
    assert loaded is not None
    assert loaded.paused is True
    assert loaded.steps[1].task_id == TASK_B
    assert loaded.steps[2].task_id is None
    assert len(hub.created) == 1


async def test_plan_advances_without_telegram_watch_store(tmp_path):
    plans = TaskPlanStore(tmp_path / "plans.json")
    hub = FakeHub()
    plan = plans.create(
        123,
        "demo.echo",
        ["collect", "publish"],
        dependencies=[[], [0]],
        now=int(time.time()),
    )
    plans.mark_submitted(plan.plan_id, 123, 0, TASK_A)
    hub.tasks[TASK_A] = {"status": "succeeded"}

    events = await advance_ready_plan_steps(hub, plans, None, {123})

    assert [(event.kind, event.step_index, event.task_id) for event in events] == [
        ("submitted", 1, TASK_B)
    ]
    loaded = plans.get_for_user(plan.plan_id, 123, now=int(time.time()) + 1)
    assert loaded is not None
    assert loaded.steps[1].task_id == TASK_B
    assert len(hub.created) == 1



def _needs_clarification_contract(status: str) -> dict[str, object]:
    return {
        "contract_version": "telegram_agent_plan.v1",
        "planning_status": status,
        "evaluation_scope": "planning_only",
        "goal_satisfaction": "not_evaluated",
        "goal": "Prepare a reviewable proposal",
        "intent": "Plan only",
        "scope": "Review proposal",
        "assumptions": [],
        "constraints": ["No execution"],
        "dependencies": [],
        "required_capabilities": [],
        "capability_availability": "not_assessed",
        "authorization_checked": False,
        "estimated_complexity": "low",
        "missing_information": ["Target environment is unknown"],
        "clarification_questions": ["Which target environment should be used?"],
        "research_required": status == "needs_research",
        "research_questions": ["Review the target environment requirements"] if status == "needs_research" else [],
        "risk_class": "low",
        "risk_class_is_authoritative": False,
        "acceptance_criteria": [
            {"criterion_id": "ac_001", "statement": "The proposal is bounded"},
            {"criterion_id": "ac_002", "statement": "The proposal can be reviewed"},
        ],
        "verification_steps": ["Inspect the proposal", "Review the criteria mapping"],
        "steps": [
            {
                "step_id": "prepare",
                "description": "Prepare bounded inputs",
                "depends_on": [],
                "required_capabilities": [],
                "verifies_criteria": ["ac_001"],
                "verification": "Inspect the proposal",
            },
            {
                "step_id": "review",
                "description": "Review proposal coverage",
                "depends_on": ["prepare"],
                "required_capabilities": [],
                "verifies_criteria": ["ac_002"],
                "verification": "Review the criteria mapping",
            },
        ],
        "execution_ready": False,
        "execution_started": False,
        "tools_executed": False,
        "tasks_created": 0,
    }


async def test_unresolved_versioned_plan_never_advances_in_background_scheduler(tmp_path):
    for status in ("needs_clarification", "needs_research"):
        plans = TaskPlanStore(tmp_path / f"{status}.json")
        hub = FakeHub()
        plan = plans.create_from_contract(
            123,
            "demo.echo",
            _needs_clarification_contract(status),
            now=int(time.time()),
        )
        # Simulate a persisted checkpoint created before the new readiness gate.
        # The scheduler must still refuse to dispatch any additional step.
        assert plans.mark_submitted(plan.plan_id, 123, 0, TASK_A) is True
        hub.tasks[TASK_A] = {"status": "succeeded"}

        events = await advance_ready_plan_steps(hub, plans, None, {123})

        assert events == ()
        assert hub.created == []
        stored = plans.get_for_user(plan.plan_id, 123, now=int(time.time()) + 1)
        assert stored is not None
        assert stored.submitted_count == 1
        assert stored.planning_status == status
