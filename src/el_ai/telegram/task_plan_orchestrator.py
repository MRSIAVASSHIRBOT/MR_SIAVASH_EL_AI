"""Dependency-gated, idempotent advancement for owner-approved Telegram task plans."""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass

from el_ai.integrations.hub import HubClient, HubClientError
from el_ai.telegram.task_plan import (
    MAX_TASK_PLAN_STEP_DISPATCH_ATTEMPTS,
    TaskPlanDispatchBudgetExceeded,
    TaskPlanDispatchDeferred,
    TaskPlanError,
    TaskPlanStore,
)
from el_ai.telegram.task_watch import (
    TERMINAL_TASK_STATUSES,
    TaskWatchError,
    TaskWatchStore,
)

LOGGER = logging.getLogger("el_ai.task_plan_orchestrator")
MAX_PLAN_CREATE_ATTEMPTS_PER_CYCLE = 3
DISPATCH_RETRY_BASE_SECONDS = 5
DISPATCH_RETRY_MAX_SECONDS = 60
_SUCCESS_STATUS = "succeeded"


def _dispatch_retry_delay_seconds(attempt_number: int) -> int:
    """Return full-jitter exponential backoff for a transient idempotent dispatch."""
    ceiling = min(
        DISPATCH_RETRY_BASE_SECONDS * (2 ** max(0, attempt_number - 1)),
        DISPATCH_RETRY_MAX_SECONDS,
    )
    return 1 + secrets.randbelow(ceiling)
_ACTIVE_WATCH_STATUSES = frozenset({"queued", "running", "waiting_approval"})


@dataclass(frozen=True)
class PlanAdvanceEvent:
    telegram_user_id: int
    plan_id: str
    step_index: int
    kind: str
    task_id: str | None = None
    reason: str | None = None


async def advance_ready_plan_steps(
    hub: HubClient,
    plans: TaskPlanStore,
    watches: TaskWatchStore | None,
    owner_ids: frozenset[int] | set[int] | tuple[int, ...],
    *,
    now: int | None = None,
) -> tuple[PlanAdvanceEvent, ...]:
    """Submit only steps whose dependencies succeeded, after explicit plan start.

    A plan is considered started only after at least one root task has a durable
    Hub task ID. Hub idempotency keys make retries safe when a response is lost.
    Failed dependencies block descendants; unavailable status never unlocks them.
    """
    events: list[PlanAdvanceEvent] = []
    dispatch_attempts = 0
    current_time = int(time.time()) if now is None else now
    if isinstance(current_time, bool) or not isinstance(current_time, int) or current_time < 0:
        raise ValueError("Task-plan orchestration time is invalid")
    for owner_id in sorted(owner_ids):
        try:
            owner_plans = plans.list_for_user(owner_id, now=current_time)
        except (TaskPlanError, ValueError):
            LOGGER.warning("Task-plan list could not be read for owner %s", owner_id)
            continue

        for plan in owner_plans:
            # A versioned GPI/AWI preview is dispatchable only after its
            # clarification/research requirements are cleared and status is ready_for_review.
            if plan.contract_version and plan.planning_status != "ready_for_review":
                continue
            if plan.submitted_count == 0 or plan.paused:
                continue

            statuses: dict[int, str] = {}
            for index, step in enumerate(plan.steps):
                if step.blocked_reason:
                    statuses[index] = "blocked"
                    continue
                if step.task_id is None:
                    continue
                try:
                    task = await hub.get_task(owner_id, step.task_id)
                except HubClientError:
                    continue
                status = task.get("status") if isinstance(task, dict) else None
                if isinstance(status, str):
                    statuses[index] = status

            for index, step in enumerate(plan.steps):
                if step.task_id is not None or step.blocked_reason:
                    continue

                waiting_for_dependencies = False
                failed_dependencies: list[int] = []
                blocked_dependencies: list[int] = []
                for dependency in step.depends_on:
                    dependency_status = statuses.get(dependency)
                    if dependency_status == "blocked":
                        blocked_dependencies.append(dependency)
                    elif (
                        dependency_status in TERMINAL_TASK_STATUSES
                        and dependency_status != _SUCCESS_STATUS
                    ):
                        failed_dependencies.append(dependency)
                    elif dependency_status != _SUCCESS_STATUS:
                        waiting_for_dependencies = True

                if blocked_dependencies or failed_dependencies:
                    blockers = tuple(sorted(set(blocked_dependencies + failed_dependencies)))
                    reason = (
                        "dependency_blocked"
                        if blocked_dependencies
                        else "dependency_failed"
                    )
                    try:
                        changed = plans.mark_blocked(
                            plan.plan_id,
                            owner_id,
                            index,
                            blocked_by=blockers,
                            reason=reason,
                        )
                    except (TaskPlanError, ValueError):
                        LOGGER.error(
                            "Could not checkpoint blocked plan step %s/%s",
                            plan.plan_id,
                            index + 1,
                        )
                        continue
                    statuses[index] = "blocked"
                    if changed:
                        events.append(PlanAdvanceEvent(
                            owner_id, plan.plan_id, index, "blocked", reason=reason
                        ))
                    continue

                if waiting_for_dependencies:
                    continue

                # Pause can be requested while an earlier Hub call in this same
                # advancement cycle is in flight. Re-read the owner-scoped plan
                # immediately before each new external dispatch so later steps
                # stop after that request completes. An already in-flight Hub
                # request cannot be recalled; its stable key keeps retries safe.
                try:
                    current_plan = plans.get_for_user(plan.plan_id, owner_id, now=current_time)
                except (TaskPlanError, ValueError):
                    LOGGER.warning(
                        "Task-plan state could not be revalidated before dispatch for %s",
                        plan.plan_id,
                    )
                    break
                if (
                    current_plan is None
                    or current_plan.paused
                    or (
                        current_plan.contract_version
                        and current_plan.planning_status != "ready_for_review"
                    )
                ):
                    break
                if index >= len(current_plan.steps):
                    break
                current_step = current_plan.steps[index]
                if current_step.task_id is not None or current_step.blocked_reason:
                    continue
                if current_step.next_dispatch_at is not None and current_step.next_dispatch_at > current_time:
                    continue
                if current_step.dispatch_attempts >= MAX_TASK_PLAN_STEP_DISPATCH_ATTEMPTS:
                    try:
                        changed = plans.mark_blocked(
                            plan.plan_id, owner_id, index,
                            reason="dispatch_attempts_exhausted",
                        )
                    except (TaskPlanError, ValueError):
                        LOGGER.error(
                            "Could not checkpoint exhausted dispatch budget for plan %s step %s",
                            plan.plan_id, index + 1,
                        )
                        continue
                    statuses[index] = "blocked"
                    if changed:
                        events.append(PlanAdvanceEvent(
                            owner_id, plan.plan_id, index, "blocked",
                            reason="dispatch_attempts_exhausted",
                        ))
                    continue
                step = current_step

                if dispatch_attempts >= MAX_PLAN_CREATE_ATTEMPTS_PER_CYCLE:
                    LOGGER.warning(
                        "Task-plan dispatch burst cap reached max_create_attempts=%s",
                        MAX_PLAN_CREATE_ATTEMPTS_PER_CYCLE,
                    )
                    return tuple(events)

                idempotency_key = f"telegram-plan-{plan.plan_id}-{index + 1}"
                try:
                    attempt_number = plans.reserve_dispatch_attempt(
                        plan.plan_id, owner_id, index, now=current_time
                    )
                except TaskPlanDispatchDeferred:
                    continue
                except TaskPlanDispatchBudgetExceeded:
                    try:
                        changed = plans.mark_blocked(
                            plan.plan_id, owner_id, index,
                            reason="dispatch_attempts_exhausted",
                        )
                    except (TaskPlanError, ValueError):
                        LOGGER.error(
                            "Could not checkpoint exhausted dispatch budget for plan %s step %s",
                            plan.plan_id, index + 1,
                        )
                        continue
                    statuses[index] = "blocked"
                    if changed:
                        events.append(PlanAdvanceEvent(
                            owner_id, plan.plan_id, index, "blocked",
                            reason="dispatch_attempts_exhausted",
                        ))
                    continue
                except (TaskPlanError, ValueError):
                    LOGGER.error(
                        "Could not reserve a durable dispatch attempt for plan %s step %s",
                        plan.plan_id, index + 1,
                    )
                    break
                # Persist the attempt before the external call so a restart cannot
                # reset a failing step's retry budget.
                dispatch_attempts += 1
                try:
                    result = await hub.create_task(
                        owner_id,
                        plan.task_type,
                        step.description,
                        idempotency_key=idempotency_key,
                    )
                except HubClientError as exc:
                    if exc.status_code in {403, 409, 422}:
                        try:
                            changed = plans.mark_blocked(
                                plan.plan_id,
                                owner_id,
                                index,
                                reason="hub_policy_denied",
                            )
                        except (TaskPlanError, ValueError):
                            LOGGER.error(
                                "Could not checkpoint Hub policy denial for plan %s step %s",
                                plan.plan_id,
                                index + 1,
                            )
                            continue
                        statuses[index] = "blocked"
                        if changed:
                            events.append(PlanAdvanceEvent(
                                owner_id,
                                plan.plan_id,
                                index,
                                "blocked",
                                reason="hub_policy_denied",
                            ))
                    else:
                        if attempt_number >= MAX_TASK_PLAN_STEP_DISPATCH_ATTEMPTS:
                            try:
                                changed = plans.mark_blocked(
                                    plan.plan_id, owner_id, index,
                                    reason="dispatch_attempts_exhausted",
                                )
                            except (TaskPlanError, ValueError):
                                LOGGER.error(
                                    "Could not checkpoint exhausted dispatch budget for plan %s step %s",
                                    plan.plan_id, index + 1,
                                )
                                continue
                            statuses[index] = "blocked"
                            if changed:
                                events.append(PlanAdvanceEvent(
                                    owner_id, plan.plan_id, index, "blocked",
                                    reason="dispatch_attempts_exhausted",
                                ))
                        else:
                            retry_after = _dispatch_retry_delay_seconds(attempt_number)
                            try:
                                plans.defer_dispatch_attempt(
                                    plan.plan_id, owner_id, index,
                                    expected_attempts=attempt_number,
                                    retry_at=current_time + retry_after,
                                )
                            except (TaskPlanError, ValueError):
                                LOGGER.error(
                                    "Could not persist dispatch backoff for plan %s step %s",
                                    plan.plan_id, index + 1,
                                )
                            LOGGER.warning(
                                "Hub task creation deferred for plan %s step %s attempt=%s retry_after_seconds=%s",
                                plan.plan_id, index + 1, attempt_number, retry_after,
                            )
                    continue

                raw_task_id = result.get("task_id") if isinstance(result, dict) else None
                try:
                    from uuid import UUID

                    task_id = str(UUID(str(raw_task_id)))
                except (ValueError, TypeError, AttributeError):
                    LOGGER.warning(
                        "Hub returned no valid task ID for plan %s step %s attempt=%s",
                        plan.plan_id, index + 1, attempt_number,
                    )
                    if attempt_number >= MAX_TASK_PLAN_STEP_DISPATCH_ATTEMPTS:
                        try:
                            changed = plans.mark_blocked(
                                plan.plan_id, owner_id, index,
                                reason="dispatch_attempts_exhausted",
                            )
                        except (TaskPlanError, ValueError):
                            LOGGER.error(
                                "Could not checkpoint exhausted dispatch budget for plan %s step %s",
                                plan.plan_id, index + 1,
                            )
                            continue
                        statuses[index] = "blocked"
                        if changed:
                            events.append(PlanAdvanceEvent(
                                owner_id, plan.plan_id, index, "blocked",
                                reason="dispatch_attempts_exhausted",
                            ))
                    else:
                        retry_after = _dispatch_retry_delay_seconds(attempt_number)
                        try:
                            plans.defer_dispatch_attempt(
                                plan.plan_id, owner_id, index,
                                expected_attempts=attempt_number,
                                retry_at=current_time + retry_after,
                            )
                        except (TaskPlanError, ValueError):
                            LOGGER.error(
                                "Could not persist dispatch backoff for plan %s step %s",
                                plan.plan_id, index + 1,
                            )
                    continue

                try:
                    plans.mark_submitted(plan.plan_id, owner_id, index, task_id)
                except (TaskPlanError, ValueError):
                    LOGGER.error(
                        "Hub task %s exists but plan checkpoint failed; retry uses the same idempotency key",
                        task_id,
                    )
                    if attempt_number >= MAX_TASK_PLAN_STEP_DISPATCH_ATTEMPTS:
                        try:
                            changed = plans.mark_blocked(
                                plan.plan_id, owner_id, index,
                                reason="dispatch_attempts_exhausted",
                            )
                        except (TaskPlanError, ValueError):
                            LOGGER.error(
                                "Could not checkpoint exhausted dispatch budget for plan %s step %s",
                                plan.plan_id, index + 1,
                            )
                            continue
                        statuses[index] = "blocked"
                        if changed:
                            events.append(PlanAdvanceEvent(
                                owner_id, plan.plan_id, index, "blocked",
                                reason="dispatch_attempts_exhausted",
                            ))
                    else:
                        retry_after = _dispatch_retry_delay_seconds(attempt_number)
                        try:
                            plans.defer_dispatch_attempt(
                                plan.plan_id, owner_id, index,
                                expected_attempts=attempt_number,
                                retry_at=current_time + retry_after,
                            )
                        except (TaskPlanError, ValueError):
                            LOGGER.error(
                                "Could not persist dispatch backoff for plan %s step %s",
                                plan.plan_id, index + 1,
                            )
                    continue

                status = result.get("status") if isinstance(result, dict) else None
                if not isinstance(status, str) or status not in _ACTIVE_WATCH_STATUSES:
                    status = "queued"
                if watches is not None:
                    try:
                        watches.add_many([(task_id, status)], owner_id)
                    except (TaskWatchError, ValueError):
                        LOGGER.warning(
                            "Task %s was submitted but could not be added to progress watches",
                            task_id,
                        )

                statuses[index] = status
                events.append(PlanAdvanceEvent(
                    owner_id, plan.plan_id, index, "submitted", task_id=task_id
                ))

    return tuple(events)
