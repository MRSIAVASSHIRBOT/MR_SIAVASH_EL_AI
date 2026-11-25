"""Deterministic, read-only evidence evaluation for owner-scoped Telegram task plans."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from el_ai.integrations.hub import HubClient, HubClientError
from el_ai.telegram.task_plan import TaskPlan

_TASK_STATUSES = frozenset({
    "queued", "running", "waiting_approval", "succeeded",
    "failed", "cancelled", "rejected",
})
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "rejected"})
_SUCCESS_STATUS = "succeeded"
_MAX_RESULT_ATTEMPTS = 100


@dataclass(frozen=True, slots=True)
class StepEvidence:
    """A bounded status-only assessment for one plan step."""

    step_index: int
    task_status: str
    evidence_status: str
    attempt_count: int | None = None
    finished_at: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "step": self.step_index + 1,
            "task_status": self.task_status,
            "evidence_status": self.evidence_status,
            "attempt_count": self.attempt_count,
            "finished_at": self.finished_at,
        }


@dataclass(frozen=True, slots=True)
class AcceptanceCriterionEvidence:
    """Execution-evidence coverage for a criterion, not semantic acceptance."""

    criterion_id: str
    evidence_status: str
    step_indexes: tuple[int, ...]

    def as_dict(self) -> dict[str, object]:
        """Expose criterion IDs and step indexes without returning private statements."""
        return {
            "criterion_id": self.criterion_id,
            "evidence_status": self.evidence_status,
            "steps": [index + 1 for index in self.step_indexes],
        }


@dataclass(frozen=True, slots=True)
class PlanEvidenceReport:
    """Read-only aggregate evidence status; it never changes or retries a plan."""

    status: str
    total_steps: int
    submitted_steps: int
    evidence_available: int
    failed_steps: int
    missing_evidence: int
    pending_steps: int
    unknown_steps: int
    blocked_steps: int
    steps: tuple[StepEvidence, ...]
    acceptance_criteria: tuple[AcceptanceCriterionEvidence, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """Serialize a bounded report without including task results or criterion text."""
        criteria_statuses = tuple(
            item.evidence_status for item in self.acceptance_criteria
        )
        if not criteria_statuses:
            criteria_coverage_status = "not_applicable"
        elif "needs_attention" in criteria_statuses:
            criteria_coverage_status = "needs_attention"
        elif any(
            status in {"insufficient_evidence", "not_mapped"}
            for status in criteria_statuses
        ):
            criteria_coverage_status = "insufficient_evidence"
        elif "unknown" in criteria_statuses:
            criteria_coverage_status = "unknown"
        elif "in_progress" in criteria_statuses:
            criteria_coverage_status = "in_progress"
        elif all(
            status == "execution_evidence_available"
            for status in criteria_statuses
        ):
            criteria_coverage_status = "execution_coverage_complete"
        else:
            criteria_coverage_status = "insufficient_evidence"

        return {
            "engine": "deterministic_task_plan_evidence_v1",
            "evaluation_scope": "tool_execution_only",
            "goal_satisfaction": "not_evaluated",
            "acceptance_criteria_evaluation": (
                "execution_coverage_only"
                if self.acceptance_criteria else "not_implemented"
            ),
            "acceptance_criteria_coverage_status": criteria_coverage_status,
            "acceptance_criteria_count": len(self.acceptance_criteria),
            "acceptance_criteria_with_execution_evidence": sum(
                status == "execution_evidence_available"
                for status in criteria_statuses
            ),
            "acceptance_criteria": [
                item.as_dict() for item in self.acceptance_criteria
            ],
            "status": self.status,
            "read_only": True,
            "automatic_retry": False,
            "automatic_correction": False,
            "total_steps": self.total_steps,
            "submitted_steps": self.submitted_steps,
            "evidence_available": self.evidence_available,
            "failed_steps": self.failed_steps,
            "missing_evidence": self.missing_evidence,
            "pending_steps": self.pending_steps,
            "unknown_steps": self.unknown_steps,
            "blocked_steps": self.blocked_steps,
            "steps": [step.as_dict() for step in self.steps],
        }


def _valid_finished_at(value: object) -> str | None:
    """Accept only bounded ISO-8601 timestamps with an explicit UTC offset."""
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return value


def _valid_result_record(value: object) -> tuple[bool, int | None, str | None]:
    """Check the Hub worker result envelope without rendering or retaining its payload."""
    if not isinstance(value, dict):
        return False, None, None
    result_payload = value.get("result")
    attempts = value.get("attempt_count")
    finished_at = _valid_finished_at(value.get("finished_at"))
    valid_attempts = (
        isinstance(attempts, int)
        and not isinstance(attempts, bool)
        and 1 <= attempts <= _MAX_RESULT_ATTEMPTS
    )
    valid_payload = isinstance(result_payload, dict) and bool(result_payload)
    valid_error = value.get("error_code") is None
    return (valid_payload and valid_attempts and valid_error and finished_at is not None), (
        attempts if valid_attempts else None
    ), finished_at


async def _read_step_evidence(
    hub: HubClient, telegram_user_id: int, step_index: int, task_id: str
) -> StepEvidence:
    """Read one owner's task and terminal result under the existing bounded API scopes."""
    try:
        task = await hub.get_task(telegram_user_id, task_id)
    except HubClientError:
        return StepEvidence(step_index, "unknown", "unknown")
    status = task.get("status") if isinstance(task, dict) else None
    if not isinstance(status, str) or status not in _TASK_STATUSES:
        return StepEvidence(step_index, "unknown", "unknown")
    if status == _SUCCESS_STATUS:
        try:
            result = await hub.get_task_result(telegram_user_id, task_id)
        except HubClientError as exc:
            evidence_status = "missing" if exc.status_code == 409 else "unknown"
            return StepEvidence(step_index, status, evidence_status)
        valid, attempts, finished_at = _valid_result_record(result)
        return StepEvidence(
            step_index, status, "available" if valid else "missing", attempts, finished_at
        )
    if status in _TERMINAL_STATUSES:
        return StepEvidence(step_index, status, "failed")
    return StepEvidence(step_index, status, "pending")


def _evaluate_acceptance_criteria(
    plan: TaskPlan, steps: tuple[StepEvidence, ...]
) -> tuple[AcceptanceCriterionEvidence, ...]:
    """Map criterion coverage to step evidence without judging result meaning."""
    evaluations: list[AcceptanceCriterionEvidence] = []
    for criterion in plan.acceptance_criteria:
        step_indexes = tuple(
            index
            for index, step in enumerate(plan.steps)
            if criterion.criterion_id in step.verifies_criteria
        )
        mapped = tuple(steps[index].evidence_status for index in step_indexes)
        if not mapped:
            evidence_status = "not_mapped"
        elif any(status in {"failed", "blocked"} for status in mapped):
            evidence_status = "needs_attention"
        elif "missing" in mapped:
            evidence_status = "insufficient_evidence"
        elif "unknown" in mapped:
            evidence_status = "unknown"
        elif any(status in {"pending", "not_submitted"} for status in mapped):
            evidence_status = "in_progress"
        elif all(status == "available" for status in mapped):
            evidence_status = "execution_evidence_available"
        else:
            evidence_status = "insufficient_evidence"
        evaluations.append(AcceptanceCriterionEvidence(
            criterion_id=criterion.criterion_id,
            evidence_status=evidence_status,
            step_indexes=step_indexes,
        ))
    return tuple(evaluations)


async def evaluate_task_plan_evidence(
    hub: HubClient, telegram_user_id: int, plan: TaskPlan
) -> PlanEvidenceReport:
    """Collect a bounded read-only evidence snapshot for a plan's submitted steps."""
    semaphore = asyncio.Semaphore(4)

    async def read_step(index: int, task_id: str | None, blocked: bool) -> StepEvidence:
        if blocked:
            return StepEvidence(index, "blocked", "blocked")
        if task_id is None:
            return StepEvidence(index, "not_submitted", "not_submitted")
        async with semaphore:
            return await _read_step_evidence(hub, telegram_user_id, index, task_id)

    steps = tuple(await asyncio.gather(*(
        read_step(index, step.task_id, bool(step.blocked_reason))
        for index, step in enumerate(plan.steps)
    )))
    available = sum(step.evidence_status == "available" for step in steps)
    failed = sum(step.evidence_status == "failed" for step in steps)
    missing = sum(step.evidence_status == "missing" for step in steps)
    pending = sum(
        step.evidence_status in {"pending", "not_submitted"} for step in steps
    )
    unknown = sum(step.evidence_status == "unknown" for step in steps)
    blocked = sum(step.evidence_status == "blocked" for step in steps)
    if failed or blocked:
        status = "needs_attention"
    elif missing:
        status = "insufficient_evidence"
    elif unknown:
        status = "unknown"
    elif pending:
        status = "in_progress"
    elif available == len(plan.steps):
        status = "evidence_complete"
    else:
        status = "insufficient_evidence"
    return PlanEvidenceReport(
        status=status,
        total_steps=len(steps),
        submitted_steps=sum(step.task_status not in {"not_submitted", "blocked"} for step in steps),
        evidence_available=available,
        failed_steps=failed,
        missing_evidence=missing,
        pending_steps=pending,
        unknown_steps=unknown,
        blocked_steps=blocked,
        steps=steps,
        acceptance_criteria=_evaluate_acceptance_criteria(plan, steps),
    )
