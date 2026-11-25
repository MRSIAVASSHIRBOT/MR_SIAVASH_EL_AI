import pytest

from el_ai.integrations.hub import HubClientError
from el_ai.telegram.task_plan import (
    TaskPlan,
    TaskPlanAcceptanceCriterion,
    TaskPlanStep,
    TaskPlanStore,
)
from el_ai.telegram.task_plan_evaluation import evaluate_task_plan_evidence


OWNER_ID = 123
TASK_A = "12345678-1234-4234-8234-123456789abc"
TASK_B = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class FakeHub:
    def __init__(self, tasks=None, results=None, errors=None, result_errors=None):
        self.tasks = tasks or {}
        self.results = results or {}
        self.errors = errors or {}
        self.result_errors = result_errors or {}
        self.task_reads = []
        self.result_reads = []

    async def get_task(self, owner_id, task_id):
        assert owner_id == OWNER_ID
        self.task_reads.append(task_id)
        if task_id in self.errors:
            raise self.errors[task_id]
        return self.tasks[task_id]

    async def get_task_result(self, owner_id, task_id):
        assert owner_id == OWNER_ID
        self.result_reads.append(task_id)
        if task_id in self.result_errors:
            raise self.result_errors[task_id]
        return self.results[task_id]


def _result_payload():
    return {
        "result": {"message": "private result text must not be returned"},
        "error_code": None,
        "attempt_count": 1,
        "started_at": "2026-11-24T10:00:00+00:00",
        "finished_at": "2026-11-24T10:00:01+00:00",
    }


def _plan(tmp_path, descriptions=("collect", "publish"), dependencies=None):
    store = TaskPlanStore(tmp_path / "plans.json")
    return store.create(
        OWNER_ID, "demo.echo", list(descriptions), dependencies=dependencies, now=100
    )


def _contract_plan():
    """Build a versioned plan with overlapping criterion-to-step mappings."""
    return TaskPlan(
        plan_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        telegram_user_id=OWNER_ID,
        task_type="demo.echo",
        created_at=100,
        steps=(
            TaskPlanStep(
                description="collect",
                task_id=TASK_A,
                step_id="collect",
                verifies_criteria=("ac_001",),
                verification="Inspect the collected input",
            ),
            TaskPlanStep(
                description="review",
                task_id=TASK_B,
                step_id="review",
                verifies_criteria=("ac_001", "ac_002"),
                verification="Check the review record",
            ),
        ),
        contract_version="telegram_agent_plan.v1",
        evaluation_scope="planning_only",
        planning_status="ready_for_review",
        acceptance_criteria=(
            TaskPlanAcceptanceCriterion(
                "ac_001", "Private criterion statement must not be returned"
            ),
            TaskPlanAcceptanceCriterion(
                "ac_002", "Another private criterion statement must not be returned"
            ),
        ),
        verification_steps=(
            "Inspect the collected input",
            "Check the review record",
        ),
    )


@pytest.mark.asyncio
async def test_plan_evidence_is_complete_only_when_every_success_has_a_valid_result(tmp_path):
    plan = _plan(tmp_path)
    store = TaskPlanStore(tmp_path / "plans.json")
    store.mark_submitted(plan.plan_id, OWNER_ID, 0, TASK_A)
    store.mark_submitted(plan.plan_id, OWNER_ID, 1, TASK_B)
    plan = store.get_for_user(plan.plan_id, OWNER_ID, now=101)
    hub = FakeHub(
        tasks={TASK_A: {"status": "succeeded"}, TASK_B: {"status": "succeeded"}},
        results={TASK_A: _result_payload(), TASK_B: _result_payload()},
    )

    report = await evaluate_task_plan_evidence(hub, OWNER_ID, plan)
    rendered = report.as_dict()

    assert rendered["status"] == "evidence_complete"
    assert rendered["evaluation_scope"] == "tool_execution_only"
    assert rendered["goal_satisfaction"] == "not_evaluated"
    assert rendered["acceptance_criteria_evaluation"] == "not_implemented"
    assert rendered["acceptance_criteria_coverage_status"] == "not_applicable"
    assert rendered["read_only"] is True
    assert rendered["automatic_retry"] is False
    assert rendered["automatic_correction"] is False
    assert rendered["evidence_available"] == 2
    assert rendered["steps"][0]["evidence_status"] == "available"
    assert rendered["steps"][0]["attempt_count"] == 1
    assert "private result text" not in str(rendered)
    assert hub.result_reads == [TASK_A, TASK_B]


@pytest.mark.asyncio
async def test_versioned_plan_reports_execution_coverage_without_claiming_goal_satisfaction():
    plan = _contract_plan()
    hub = FakeHub(
        tasks={TASK_A: {"status": "succeeded"}, TASK_B: {"status": "succeeded"}},
        results={TASK_A: _result_payload(), TASK_B: _result_payload()},
    )

    rendered = (
        await evaluate_task_plan_evidence(hub, OWNER_ID, plan)
    ).as_dict()

    assert rendered["acceptance_criteria_evaluation"] == "execution_coverage_only"
    assert rendered["acceptance_criteria_coverage_status"] == "execution_coverage_complete"
    assert rendered["acceptance_criteria_count"] == 2
    assert rendered["acceptance_criteria_with_execution_evidence"] == 2
    assert rendered["acceptance_criteria"] == [
        {
            "criterion_id": "ac_001",
            "evidence_status": "execution_evidence_available",
            "steps": [1, 2],
        },
        {
            "criterion_id": "ac_002",
            "evidence_status": "execution_evidence_available",
            "steps": [2],
        },
    ]
    assert rendered["goal_satisfaction"] == "not_evaluated"
    assert "Private criterion statement" not in str(rendered)
    assert "private result text" not in str(rendered)
    assert rendered["read_only"] is True
    assert rendered["automatic_retry"] is False
    assert rendered["automatic_correction"] is False


@pytest.mark.asyncio
async def test_criterion_execution_coverage_requires_every_mapped_result():
    plan = _contract_plan()
    malformed = _result_payload() | {"attempt_count": True}
    hub = FakeHub(
        tasks={TASK_A: {"status": "succeeded"}, TASK_B: {"status": "succeeded"}},
        results={TASK_A: _result_payload(), TASK_B: malformed},
    )

    rendered = (
        await evaluate_task_plan_evidence(hub, OWNER_ID, plan)
    ).as_dict()

    assert rendered["acceptance_criteria_coverage_status"] == "insufficient_evidence"
    assert rendered["acceptance_criteria"][0]["evidence_status"] == "insufficient_evidence"
    assert rendered["acceptance_criteria"][1]["evidence_status"] == "insufficient_evidence"
    assert rendered["acceptance_criteria_with_execution_evidence"] == 0
    assert rendered["goal_satisfaction"] == "not_evaluated"


@pytest.mark.asyncio
async def test_failed_mapped_step_requires_attention_for_each_affected_criterion():
    plan = _contract_plan()
    hub = FakeHub(
        tasks={TASK_A: {"status": "succeeded"}, TASK_B: {"status": "failed"}},
        results={TASK_A: _result_payload()},
    )

    rendered = (
        await evaluate_task_plan_evidence(hub, OWNER_ID, plan)
    ).as_dict()

    assert rendered["acceptance_criteria_coverage_status"] == "needs_attention"
    assert [item["evidence_status"] for item in rendered["acceptance_criteria"]] == [
        "needs_attention", "needs_attention"
    ]
    assert rendered["goal_satisfaction"] == "not_evaluated"


@pytest.mark.asyncio
async def test_unknown_or_pending_mapped_step_never_becomes_criterion_success():
    unknown_plan = _contract_plan()
    unknown_hub = FakeHub(
        tasks={TASK_A: {"status": "succeeded"}},
        results={TASK_A: _result_payload()},
        errors={TASK_B: HubClientError("temporarily unavailable", status_code=503)},
    )
    unknown = (
        await evaluate_task_plan_evidence(unknown_hub, OWNER_ID, unknown_plan)
    ).as_dict()
    assert unknown["acceptance_criteria_coverage_status"] == "unknown"
    assert [item["evidence_status"] for item in unknown["acceptance_criteria"]] == [
        "unknown", "unknown"
    ]

    pending_plan = _contract_plan()
    pending_hub = FakeHub(
        tasks={TASK_A: {"status": "succeeded"}, TASK_B: {"status": "running"}},
        results={TASK_A: _result_payload()},
    )
    pending = (
        await evaluate_task_plan_evidence(pending_hub, OWNER_ID, pending_plan)
    ).as_dict()
    assert pending["acceptance_criteria_coverage_status"] == "in_progress"
    assert [item["evidence_status"] for item in pending["acceptance_criteria"]] == [
        "in_progress", "in_progress"
    ]


@pytest.mark.asyncio
async def test_succeeded_task_without_result_is_insufficient_evidence(tmp_path):
    plan = _plan(tmp_path)
    store = TaskPlanStore(tmp_path / "plans.json")
    store.mark_submitted(plan.plan_id, OWNER_ID, 0, TASK_A)
    plan = store.get_for_user(plan.plan_id, OWNER_ID, now=101)
    hub = FakeHub(
        tasks={TASK_A: {"status": "succeeded"}},
        result_errors={TASK_A: HubClientError("result not ready", status_code=409)},
    )

    report = await evaluate_task_plan_evidence(hub, OWNER_ID, plan)

    assert report.status == "insufficient_evidence"
    assert report.missing_evidence == 1


@pytest.mark.asyncio
async def test_failed_or_blocked_step_requires_attention_without_fetching_results(tmp_path):
    store = TaskPlanStore(tmp_path / "plans.json")
    plan = store.create(
        OWNER_ID, "demo.echo", ["first", "second"], dependencies=[[], [0]], now=100
    )
    store.mark_submitted(plan.plan_id, OWNER_ID, 0, TASK_A)
    store.mark_blocked(plan.plan_id, OWNER_ID, 1, blocked_by=[0], reason="dependency_failed")
    plan = store.get_for_user(plan.plan_id, OWNER_ID, now=101)
    hub = FakeHub(tasks={TASK_A: {"status": "failed"}})

    report = await evaluate_task_plan_evidence(hub, OWNER_ID, plan)

    assert report.status == "needs_attention"
    assert report.failed_steps == 1
    assert report.blocked_steps == 1
    assert hub.result_reads == []


@pytest.mark.asyncio
async def test_pending_or_unsubmitted_steps_keep_the_plan_in_progress(tmp_path):
    plan = _plan(tmp_path)
    store = TaskPlanStore(tmp_path / "plans.json")
    store.mark_submitted(plan.plan_id, OWNER_ID, 0, TASK_A)
    plan = store.get_for_user(plan.plan_id, OWNER_ID, now=101)
    hub = FakeHub(tasks={TASK_A: {"status": "running"}})

    report = await evaluate_task_plan_evidence(hub, OWNER_ID, plan)

    assert report.status == "in_progress"
    assert report.pending_steps == 2
    assert hub.result_reads == []


@pytest.mark.asyncio
async def test_unavailable_task_status_is_unknown_and_never_reported_as_success(tmp_path):
    plan = _plan(tmp_path)
    store = TaskPlanStore(tmp_path / "plans.json")
    store.mark_submitted(plan.plan_id, OWNER_ID, 0, TASK_A)
    plan = store.get_for_user(plan.plan_id, OWNER_ID, now=101)
    hub = FakeHub(errors={TASK_A: HubClientError("temporary", status_code=503)})

    report = await evaluate_task_plan_evidence(hub, OWNER_ID, plan)

    assert report.status == "unknown"
    assert report.unknown_steps == 1


@pytest.mark.asyncio
async def test_success_with_malformed_result_envelope_is_not_verified(tmp_path):
    plan = _plan(tmp_path)
    store = TaskPlanStore(tmp_path / "plans.json")
    store.mark_submitted(plan.plan_id, OWNER_ID, 0, TASK_A)
    plan = store.get_for_user(plan.plan_id, OWNER_ID, now=101)
    malformed = _result_payload() | {"attempt_count": True, "finished_at": "2026-11-24T10:00:01"}
    hub = FakeHub(
        tasks={TASK_A: {"status": "succeeded"}},
        results={TASK_A: malformed},
    )

    report = await evaluate_task_plan_evidence(hub, OWNER_ID, plan)

    assert report.status == "insufficient_evidence"
    assert report.missing_evidence == 1
