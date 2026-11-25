from __future__ import annotations

import json
import time

from el_ai.integrations.hub import HubClientError
from el_ai.telegram.task_plan import (
    MAX_TASK_PLAN_STEP_DISPATCH_ATTEMPTS,
    TaskPlanStore,
)
from el_ai.telegram.task_plan_orchestrator import (
    MAX_PLAN_CREATE_ATTEMPTS_PER_CYCLE,
    advance_ready_plan_steps,
)
from el_ai.telegram.task_watch import TaskWatchStore

TASK_A = "12345678-1234-4234-8234-123456789abc"
TASK_B = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TASK_C = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class FakeHub:
    def __init__(self):
        self.tasks = {}
        self.created = []
        self.create_attempts = 0
        self.get_calls = []
        self.fail_create = None
        self.on_create = None

    async def get_task(self, _owner_id, task_id):
        self.get_calls.append(task_id)
        try:
            return self.tasks[task_id]
        except KeyError:
            raise HubClientError("task status is temporarily unavailable") from None

    async def create_task(self, _owner_id, task_type, description, *, idempotency_key):
        self.create_attempts += 1
        if self.fail_create is not None:
            raise self.fail_create
        existing = next(
            (item for item in self.created if item["idempotency_key"] == idempotency_key),
            None,
        )
        if existing is not None:
            return {"task_id": existing["task_id"], "status": self.tasks[existing["task_id"]]["status"]}
        task_id = (TASK_B, TASK_C)[len(self.created)] if len(self.created) < 2 else (
            f"cccccccc-cccc-4ccc-8ccc-{len(self.created) + 1:012x}"
        )
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


async def test_dispatch_attempt_burst_cap_is_global_across_plans_and_owners(tmp_path):
    plans = TaskPlanStore(tmp_path / "plans.json")
    hub = FakeHub()
    hub.tasks[TASK_A] = {"status": "succeeded"}
    plan_ids = []
    for index in range(4):
        owner_id = 123 if index < 2 else 456
        plan = plans.create(
            owner_id,
            "demo.echo",
            [f"root-{index}", f"follow-up-{index}"],
            dependencies=[[], []],
            now=int(time.time()) + index,
        )
        assert plans.mark_submitted(plan.plan_id, owner_id, 0, TASK_A) is True
        plan_ids.append((owner_id, plan.plan_id))

    events = await advance_ready_plan_steps(hub, plans, None, {123, 456})

    assert MAX_PLAN_CREATE_ATTEMPTS_PER_CYCLE == 3
    assert hub.create_attempts == 3
    assert len(hub.created) == 3
    assert len([event for event in events if event.kind == "submitted"]) == 3
    remaining = [
        plans.get_for_user(plan_id, owner_id, now=int(time.time()) + 10)
        for owner_id, plan_id in plan_ids
    ]
    assert sorted(plan.submitted_count for plan in remaining) == [1, 2, 2, 2]


async def test_failed_create_attempts_consume_the_per_cycle_burst_budget(tmp_path):
    plans = TaskPlanStore(tmp_path / "plans.json")
    hub = FakeHub()
    hub.tasks[TASK_A] = {"status": "succeeded"}
    for index in range(4):
        plan = plans.create(
            123,
            "demo.echo",
            [f"root-{index}", f"follow-up-{index}"],
            dependencies=[[], []],
            now=int(time.time()) + index,
        )
        assert plans.mark_submitted(plan.plan_id, 123, 0, TASK_A) is True
    hub.fail_create = HubClientError("temporarily unavailable", status_code=503)

    events = await advance_ready_plan_steps(hub, plans, None, {123})

    assert events == ()
    assert hub.create_attempts == MAX_PLAN_CREATE_ATTEMPTS_PER_CYCLE
    assert hub.created == []


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
        "missing_information": ["Target environment is unknown"] if status == "needs_clarification" else [],
        "clarification_questions": ["Which target environment should be used?"] if status == "needs_clarification" else [],
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
        path = tmp_path / f"{status}.json"
        plans = TaskPlanStore(path)
        hub = FakeHub()
        # Start from a ready contract to create a legitimate prior checkpoint,
        # then emulate a durable state transition to an unresolved plan.
        contract = _needs_clarification_contract("ready_for_review")
        plan = plans.create_from_contract(
            123, "demo.echo", contract, now=int(time.time())
        )
        assert plans.mark_submitted(plan.plan_id, 123, 0, TASK_A) is True
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["plans"][0]["planning_status"] = status
        path.write_text(json.dumps(payload), encoding="utf-8")

        restored = TaskPlanStore(path)
        hub.tasks[TASK_A] = {"status": "succeeded"}
        events = await advance_ready_plan_steps(hub, restored, None, {123})

        assert events == ()
        assert hub.get_calls == []
        assert hub.created == []
        stored = restored.get_for_user(plan.plan_id, 123, now=int(time.time()) + 1)
        assert stored is not None
        assert stored.submitted_count == 1
        assert stored.planning_status == status


async def test_non_ready_contract_never_advances_even_with_legacy_submitted_checkpoint(tmp_path):
    path = tmp_path / "plan-needs-clarification.json"
    plans = TaskPlanStore(path)
    contract = _needs_clarification_contract("ready_for_review")
    plan = plans.create_from_contract(
        123, "demo.echo", contract, now=int(time.time())
    )
    assert plans.mark_submitted(plan.plan_id, 123, 0, TASK_A) is True

    # Simulate a persisted plan becoming unresolved after an old/legacy dispatch.
    # The scheduler must re-check readiness before reading status or dispatching.
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["plans"][0]["planning_status"] = "needs_clarification"
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = TaskPlanStore(path)
    hub = FakeHub()
    hub.tasks[TASK_A] = {"status": "succeeded"}

    events = await advance_ready_plan_steps(hub, restored, None, {123})

    assert events == ()
    assert hub.get_calls == []
    assert hub.created == []
    current = restored.get_for_user(plan.plan_id, 123, now=int(time.time()) + 1)
    assert current is not None
    assert current.planning_status == "needs_clarification"
    assert current.submitted_count == 1



async def test_transient_dispatch_failures_use_persisted_jitter_and_exhaust_budget(
    tmp_path, monkeypatch
):
    from el_ai.telegram import task_plan_orchestrator

    monkeypatch.setattr(
        task_plan_orchestrator.secrets, "randbelow",
        lambda upper_bound: upper_bound - 1,
    )
    plans = TaskPlanStore(tmp_path / "plans.json")
    hub = FakeHub()
    plan = plans.create(123, "demo.echo", ["root", "follow-up"], now=100)
    plans.mark_submitted(plan.plan_id, 123, 0, TASK_A)
    hub.tasks[TASK_A] = {"status": "succeeded"}
    hub.fail_create = HubClientError("temporarily unavailable", status_code=503)

    expected_retry_at = 100
    for attempt in range(1, MAX_TASK_PLAN_STEP_DISPATCH_ATTEMPTS + 1):
        events = await advance_ready_plan_steps(
            hub, plans, None, {123}, now=expected_retry_at
        )
        loaded = plans.get_for_user(plan.plan_id, 123, now=expected_retry_at)
        assert loaded is not None
        step = loaded.steps[1]
        assert step.dispatch_attempts == attempt
        if attempt < MAX_TASK_PLAN_STEP_DISPATCH_ATTEMPTS:
            ceiling = min(5 * (2 ** (attempt - 1)), 60)
            expected_retry_at += ceiling
            assert step.next_dispatch_at == expected_retry_at
            assert events == ()
            before = hub.create_attempts
            skipped = await advance_ready_plan_steps(
                hub, plans, None, {123}, now=expected_retry_at - 1
            )
            assert skipped == ()
            assert hub.create_attempts == before
        else:
            assert step.blocked_reason == "dispatch_attempts_exhausted"
            assert step.next_dispatch_at is None
            assert [(event.kind, event.reason) for event in events] == [
                ("blocked", "dispatch_attempts_exhausted")
            ]

    assert hub.create_attempts == MAX_TASK_PLAN_STEP_DISPATCH_ATTEMPTS
    assert await advance_ready_plan_steps(
        hub, plans, None, {123}, now=expected_retry_at + 1
    ) == ()
    assert hub.create_attempts == MAX_TASK_PLAN_STEP_DISPATCH_ATTEMPTS



async def test_restarted_dispatch_waits_for_reserved_deadline_then_reconciles_idempotently(
    tmp_path,
):
    plans = TaskPlanStore(tmp_path / "plans.json")
    hub = FakeHub()
    plan = plans.create(123, "demo.echo", ["root", "follow-up"], now=100)
    plans.mark_submitted(plan.plan_id, 123, 0, TASK_A)
    hub.tasks[TASK_A] = {"status": "succeeded"}

    # Simulate a crash after the attempt was reserved but before its response
    # could be checkpointed locally.
    assert plans.reserve_dispatch_attempt(
        plan.plan_id, 123, 1, now=100, retry_at=110
    ) == 1

    before_deadline = await advance_ready_plan_steps(
        hub, plans, None, {123}, now=109
    )
    assert before_deadline == ()
    assert hub.create_attempts == 0

    after_deadline = await advance_ready_plan_steps(
        hub, plans, None, {123}, now=110
    )
    assert [(event.kind, event.step_index, event.task_id) for event in after_deadline] == [
        ("submitted", 1, TASK_B)
    ]
    restored = plans.get_for_user(plan.plan_id, 123, now=110)
    assert restored is not None
    assert restored.steps[1].task_id == TASK_B
    assert restored.steps[1].next_dispatch_at is None
    assert restored.steps[1].dispatch_attempts == 2
