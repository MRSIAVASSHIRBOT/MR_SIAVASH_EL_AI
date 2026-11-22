"""Dependency-gated, idempotent advancement for owner-approved Telegram task plans."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from el_ai.integrations.hub import HubClient, HubClientError
from el_ai.telegram.task_plan import TaskPlanError, TaskPlanStore
from el_ai.telegram.task_watch import (
    TERMINAL_TASK_STATUSES,
    TaskWatchError,
    TaskWatchStore,
)

LOGGER = logging.getLogger("el_ai.task_plan_orchestrator")
_SUCCESS_STATUS = "succeeded"
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
    watches: TaskWatchStore,
    owner_ids: frozenset[int] | set[int] | tuple[int, ...],
) -> tuple[PlanAdvanceEvent, ...]:
    """Submit only steps whose dependencies succeeded, after explicit plan start.

    A plan is considered started only after at least one root task has a durable
    Hub task ID. Hub idempotency keys make retries safe when a response is lost.
    Failed dependencies block descendants; unavailable status never unlocks them.
    """
    events: list[PlanAdvanceEvent] = []
    for owner_id in sorted(owner_ids):
        try:
            owner_plans = plans.list_for_user(owner_id)
        except (TaskPlanError, ValueError):
            LOGGER.warning("Task-plan list could not be read for owner %s", owner_id)
            continue

        for plan in owner_plans:
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

                idempotency_key = f"telegram-plan-{plan.plan_id}-{index + 1}"
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
                        LOGGER.warning(
                            "Hub task creation deferred for plan %s step %s",
                            plan.plan_id,
                            index + 1,
                        )
                    continue

                raw_task_id = result.get("task_id") if isinstance(result, dict) else None
                try:
                    from uuid import UUID

                    task_id = str(UUID(str(raw_task_id)))
                except (ValueError, TypeError, AttributeError):
                    LOGGER.warning(
                        "Hub returned no valid task ID for plan %s step %s; idempotent retry remains safe",
                        plan.plan_id,
                        index + 1,
                    )
                    continue

                try:
                    plans.mark_submitted(plan.plan_id, owner_id, index, task_id)
                except (TaskPlanError, ValueError):
                    LOGGER.error(
                        "Hub task %s exists but plan checkpoint failed; retry uses the same idempotency key",
                        task_id,
                    )
                    continue

                status = result.get("status") if isinstance(result, dict) else None
                if not isinstance(status, str) or status not in _ACTIVE_WATCH_STATUSES:
                    status = "queued"
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
