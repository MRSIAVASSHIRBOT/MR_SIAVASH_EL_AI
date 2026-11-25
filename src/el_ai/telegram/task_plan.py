"""Private, bounded Telegram task-plan drafts with resumable idempotent submission."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4


MAX_TASK_PLANS = 10
MIN_TASK_PLAN_STEPS = 2
MAX_TASK_PLAN_STEPS = 8
MAX_TASK_PLAN_BYTES = 256 * 1024
MAX_TASK_PLAN_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_TASK_TYPE_LENGTH = 80
MAX_TASK_STEP_LENGTH = 400
_TASK_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


class TaskPlanError(RuntimeError):
    """Raised when a local task-plan draft is invalid or cannot be persisted."""


@dataclass(frozen=True)
class TaskPlanAcceptanceCriterion:
    criterion_id: str
    statement: str


@dataclass(frozen=True)
class TaskPlanStep:
    description: str
    task_id: str | None = None
    depends_on: tuple[int, ...] = ()
    blocked_by: tuple[int, ...] = ()
    blocked_reason: str | None = None
    step_id: str | None = None
    verifies_criteria: tuple[str, ...] = ()
    verification: str | None = None


@dataclass(frozen=True)
class TaskPlan:
    plan_id: str
    telegram_user_id: int
    task_type: str
    created_at: int
    steps: tuple[TaskPlanStep, ...]
    paused: bool = False
    contract_version: str | None = None
    evaluation_scope: str | None = None
    planning_status: str | None = None
    acceptance_criteria: tuple[TaskPlanAcceptanceCriterion, ...] = ()
    verification_steps: tuple[str, ...] = ()
    goal_satisfaction: str = "not_evaluated"
    capability_availability: str = "not_assessed"
    authorization_checked: bool = False

    @property
    def submitted_count(self) -> int:
        return sum(step.task_id is not None for step in self.steps)


class TaskPlanStore:
    """Persist owner-authored plans without credentials; files are private and bounded."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._plans: list[TaskPlan] | None = None

    def load(self) -> tuple[TaskPlan, ...]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._path, flags)
        except FileNotFoundError:
            self._plans = []
            return ()
        except OSError as exc:
            raise TaskPlanError("Could not safely open Telegram task-plan state") from exc

        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_TASK_PLAN_BYTES:
                raise TaskPlanError("Telegram task-plan state is not a bounded regular file")
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                raw = handle.read(MAX_TASK_PLAN_BYTES + 1)
            if len(raw.encode("utf-8")) > MAX_TASK_PLAN_BYTES:
                raise TaskPlanError("Telegram task-plan state exceeds its size limit")
            payload = json.loads(raw)
        except TaskPlanError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TaskPlanError("Stored Telegram task-plan state is invalid") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)

        if (
            not isinstance(payload, dict)
            or set(payload) != {"version", "plans"}
            or isinstance(payload.get("version"), bool)
            or not isinstance(payload.get("version"), int)
            or payload.get("version") not in {1, 2, 3, 4}
            or not isinstance(payload.get("plans"), list)
        ):
            raise TaskPlanError("Stored Telegram task-plan state is unsupported")

        plans: list[TaskPlan] = []
        seen: set[str] = set()
        for item in payload["plans"]:
            expected_plan_keys = {
                "plan_id", "telegram_user_id", "task_type", "created_at", "steps"
            }
            if payload["version"] >= 3:
                expected_plan_keys.add("paused")
            if payload["version"] == 4:
                expected_plan_keys.update({
                    "contract_version", "evaluation_scope", "planning_status",
                    "acceptance_criteria", "verification_steps", "goal_satisfaction",
                    "capability_availability", "authorization_checked",
                })
            if not isinstance(item, dict) or set(item) != expected_plan_keys:
                raise TaskPlanError("Stored Telegram task-plan entry is invalid")
            try:
                plan_id = self._canonical_uuid(item.get("plan_id"), "plan ID")
                user_id = item.get("telegram_user_id")
                task_type = item.get("task_type")
                created_at = item.get("created_at")
                paused = item.get("paused", False)
                raw_steps = item.get("steps")
                contract_version = item.get("contract_version")
                evaluation_scope = item.get("evaluation_scope")
                planning_status = item.get("planning_status")
                raw_criteria = item.get("acceptance_criteria", [])
                raw_verification_steps = item.get("verification_steps", [])
                goal_satisfaction = item.get("goal_satisfaction", "not_evaluated")
                capability_availability = item.get("capability_availability", "not_assessed")
                authorization_checked = item.get("authorization_checked", False)
                if not isinstance(paused, bool):
                    raise TaskPlanError("Stored Telegram task-plan pause state is invalid")
                self._validate_user_id(user_id)
                self._validate_task_type(task_type)
                self._validate_timestamp(created_at)
                if not isinstance(raw_steps, list) or not (
                    MIN_TASK_PLAN_STEPS <= len(raw_steps) <= MAX_TASK_PLAN_STEPS
                ):
                    raise TaskPlanError("Stored Telegram task-plan step count is invalid")
                steps: list[TaskPlanStep] = []
                for step_index, raw_step in enumerate(raw_steps):
                    expected_keys = (
                        {"description", "task_id"}
                        if payload["version"] == 1
                        else {"description", "task_id", "depends_on", "blocked_by", "blocked_reason"}
                    )
                    if payload["version"] == 4:
                        expected_keys.update({"step_id", "verifies_criteria", "verification"})
                    if not isinstance(raw_step, dict) or set(raw_step) != expected_keys:
                        raise TaskPlanError("Stored Telegram task-plan step is invalid")
                    description = self._validate_description(raw_step.get("description"))
                    task_id = raw_step.get("task_id")
                    if task_id is not None:
                        task_id = self._canonical_uuid(task_id, "task ID")
                    step_id = raw_step.get("step_id") if payload["version"] == 4 else None
                    verifies_criteria = raw_step.get("verifies_criteria", []) if payload["version"] == 4 else []
                    verification = raw_step.get("verification") if payload["version"] == 4 else None
                    if payload["version"] == 1:
                        dependencies = () if step_index == 0 else (step_index - 1,)
                        blocked_by = ()
                        blocked_reason = None
                    else:
                        dependencies = self._validate_dependency_group(
                            raw_step.get("depends_on"), step_index
                        )
                        blocked_by = self._validate_blocked_by(
                            raw_step.get("blocked_by"), dependencies
                        )
                        blocked_reason = self._validate_blocked_reason(
                            raw_step.get("blocked_reason")
                        )
                    if blocked_by and not blocked_reason:
                        raise TaskPlanError("Stored blocked task-plan step has no reason")
                    if blocked_reason and task_id is not None:
                        raise TaskPlanError("A submitted task-plan step cannot be blocked")
                    normalized_step_id, normalized_coverage, normalized_verification = (
                        self._validate_step_contract_fields(
                            step_id, verifies_criteria, verification
                        )
                    )
                    steps.append(TaskPlanStep(
                        description, task_id, dependencies, blocked_by, blocked_reason,
                        normalized_step_id, normalized_coverage, normalized_verification,
                    ))
            except ValueError as exc:
                raise TaskPlanError("Stored Telegram task-plan entry is invalid") from exc
            acceptance_criteria = self._validate_acceptance_criteria(raw_criteria)
            verification_steps = self._validate_verification_steps(
                raw_verification_steps, allow_empty=True
            )
            self._validate_stored_plan_contract(
                contract_version=contract_version,
                evaluation_scope=evaluation_scope,
                planning_status=planning_status,
                acceptance_criteria=acceptance_criteria,
                verification_steps=verification_steps,
                goal_satisfaction=goal_satisfaction,
                capability_availability=capability_availability,
                authorization_checked=authorization_checked,
                steps=tuple(steps),
            )
            if plan_id in seen:
                raise TaskPlanError("Stored Telegram task plans are duplicated")
            seen.add(plan_id)
            plans.append(TaskPlan(
                plan_id=plan_id,
                telegram_user_id=user_id,
                task_type=task_type,
                created_at=created_at,
                steps=tuple(steps),
                paused=paused,
                contract_version=contract_version,
                evaluation_scope=evaluation_scope,
                planning_status=planning_status,
                acceptance_criteria=acceptance_criteria,
                verification_steps=verification_steps,
                goal_satisfaction=goal_satisfaction,
                capability_availability=capability_availability,
                authorization_checked=authorization_checked,
            ))

        if len(plans) > MAX_TASK_PLANS:
            raise TaskPlanError("Stored Telegram task-plan collection exceeds its limit")
        self._plans = plans
        if payload["version"] < 4:
            # One-way migration: v1-v3 plans gain explicit non-evaluation defaults.
            self._replace(plans)
        return tuple(plans)

    def create(
        self,
        telegram_user_id: int,
        task_type: str,
        descriptions: list[str] | tuple[str, ...],
        *,
        dependencies: list[list[int]] | tuple[tuple[int, ...], ...] | None = None,
        now: int | None = None,
        plan_contract: dict[str, object] | None = None,
    ) -> TaskPlan:
        self._validate_user_id(telegram_user_id)
        self._validate_task_type(task_type)
        if not isinstance(descriptions, (list, tuple)) or not (
            MIN_TASK_PLAN_STEPS <= len(descriptions) <= MAX_TASK_PLAN_STEPS
        ):
            raise TaskPlanError(
                f"A task plan must contain {MIN_TASK_PLAN_STEPS}–{MAX_TASK_PLAN_STEPS} steps"
            )
        normalized_descriptions = tuple(
            self._validate_description(item) for item in descriptions
        )
        if plan_contract is None:
            normalized_dependencies = self._normalize_dependencies(
                dependencies, len(normalized_descriptions)
            )
            step_contract = tuple((None, (), None) for _ in normalized_descriptions)
            contract_metadata: dict[str, object] = {}
        else:
            contract_dependencies, step_contract, contract_metadata = self._validate_plan_contract(
                plan_contract, normalized_descriptions
            )
            if dependencies is not None:
                supplied_dependencies = self._normalize_dependencies(
                    dependencies, len(normalized_descriptions)
                )
                if supplied_dependencies != contract_dependencies:
                    raise TaskPlanError("Supplied dependencies do not match the Hub plan contract")
            normalized_dependencies = contract_dependencies
        steps = tuple(
            TaskPlanStep(
                normalized_descriptions[index],
                depends_on=normalized_dependencies[index],
                step_id=step_contract[index][0],
                verifies_criteria=step_contract[index][1],
                verification=step_contract[index][2],
            )
            for index in range(len(normalized_descriptions))
        )
        current_time = int(time.time()) if now is None else now
        self._validate_timestamp(current_time)
        self._prune_expired(current_time)
        plans = self._ensure_loaded()
        if len(plans) >= MAX_TASK_PLANS:
            raise TaskPlanError("The Telegram task-plan limit has been reached")
        plan = TaskPlan(
            plan_id=str(uuid4()),
            telegram_user_id=telegram_user_id,
            task_type=task_type,
            created_at=current_time,
            steps=steps,
            **contract_metadata,
        )
        self._replace(plans + [plan])
        return plan

    def create_from_contract(
        self,
        telegram_user_id: int,
        task_type: str,
        plan_contract: object,
        *,
        now: int | None = None,
    ) -> TaskPlan:
        """Persist only a validated telegram_agent_plan.v1 preview; never executes it."""
        if not isinstance(plan_contract, dict) or not isinstance(plan_contract.get("steps"), list):
            raise TaskPlanError("Hub plan contract is missing its step list")
        descriptions: list[str] = []
        for item in plan_contract["steps"]:
            if not isinstance(item, dict):
                raise TaskPlanError("Hub plan contract step is invalid")
            descriptions.append(self._validate_description(item.get("description")))
        return self.create(
            telegram_user_id,
            task_type,
            descriptions,
            now=now,
            plan_contract=plan_contract,
        )

    def get_for_user(
        self,
        plan_id: str,
        telegram_user_id: int,
        *,
        now: int | None = None,
    ) -> TaskPlan | None:
        canonical_id = self._canonical_uuid(plan_id, "plan ID")
        self._validate_user_id(telegram_user_id)
        current_time = int(time.time()) if now is None else now
        self._validate_timestamp(current_time)
        self._prune_expired(current_time)
        return next(
            (
                plan for plan in self._ensure_loaded()
                if plan.plan_id == canonical_id
                and plan.telegram_user_id == telegram_user_id
            ),
            None,
        )

    def list_for_user(
        self,
        telegram_user_id: int,
        *,
        now: int | None = None,
    ) -> tuple[TaskPlan, ...]:
        self._validate_user_id(telegram_user_id)
        current_time = int(time.time()) if now is None else now
        self._validate_timestamp(current_time)
        self._prune_expired(current_time)
        return tuple(sorted(
            (
                plan for plan in self._ensure_loaded()
                if plan.telegram_user_id == telegram_user_id
            ),
            key=lambda plan: (plan.created_at, plan.plan_id),
            reverse=True,
        ))

    def mark_submitted(
        self,
        plan_id: str,
        telegram_user_id: int,
        step_index: int,
        task_id: str,
    ) -> bool:
        canonical_plan_id = self._canonical_uuid(plan_id, "plan ID")
        canonical_task_id = self._canonical_uuid(task_id, "task ID")
        self._validate_user_id(telegram_user_id)
        if isinstance(step_index, bool) or not isinstance(step_index, int):
            raise ValueError("Task-plan step index is invalid")
        plans = self._ensure_loaded()
        updated: list[TaskPlan] = []
        changed = False
        found = False
        for plan in plans:
            if plan.plan_id != canonical_plan_id or plan.telegram_user_id != telegram_user_id:
                updated.append(plan)
                continue
            found = True
            if not 0 <= step_index < len(plan.steps):
                raise ValueError("Task-plan step index is out of range")
            steps = list(plan.steps)
            current_step = steps[step_index]
            existing = current_step.task_id
            if existing == canonical_task_id:
                updated.append(plan)
                continue
            if plan.contract_version and plan.planning_status != "ready_for_review":
                raise TaskPlanError(
                    "A versioned task plan cannot submit until planning is ready for review"
                )
            if existing is not None:
                raise TaskPlanError("A different task is already recorded for this plan step")
            if current_step.blocked_reason or current_step.blocked_by:
                raise TaskPlanError("A blocked task-plan step cannot be submitted")
            if any(steps[dependency].task_id is None for dependency in current_step.depends_on):
                raise TaskPlanError("Task-plan dependencies must be submitted before this step")
            steps[step_index] = replace(current_step, task_id=canonical_task_id)
            updated.append(replace(plan, steps=tuple(steps)))
            changed = True
        if not found:
            raise TaskPlanError("Task plan is unavailable")
        if changed:
            self._replace(updated)
        return changed

    def mark_blocked(
        self,
        plan_id: str,
        telegram_user_id: int,
        step_index: int,
        *,
        blocked_by: list[int] | tuple[int, ...] = (),
        reason: str,
    ) -> bool:
        canonical_plan_id = self._canonical_uuid(plan_id, "plan ID")
        self._validate_user_id(telegram_user_id)
        if isinstance(step_index, bool) or not isinstance(step_index, int):
            raise ValueError("Task-plan step index is invalid")
        if not isinstance(blocked_by, (list, tuple)):
            raise ValueError("Blocked dependency indexes must be a list")
        normalized_reason = self._validate_blocked_reason(reason)
        if normalized_reason is None:
            raise ValueError("A blocked task-plan step requires a reason")
        plans = self._ensure_loaded()
        updated: list[TaskPlan] = []
        changed = False
        found = False
        for plan in plans:
            if plan.plan_id != canonical_plan_id or plan.telegram_user_id != telegram_user_id:
                updated.append(plan)
                continue
            found = True
            if not 0 <= step_index < len(plan.steps):
                raise ValueError("Task-plan step index is out of range")
            steps = list(plan.steps)
            step = steps[step_index]
            normalized_blockers = self._validate_blocked_by(list(blocked_by), step.depends_on)
            if step.task_id is not None:
                raise TaskPlanError("A submitted task-plan step cannot be blocked")
            if step.blocked_reason == normalized_reason and step.blocked_by == normalized_blockers:
                updated.append(plan)
                continue
            steps[step_index] = replace(
                step,
                blocked_by=normalized_blockers,
                blocked_reason=normalized_reason,
            )
            updated.append(replace(plan, steps=tuple(steps)))
            changed = True
        if not found:
            raise TaskPlanError("Task plan is unavailable")
        if changed:
            self._replace(updated)
        return changed

    def set_paused(
        self,
        plan_id: str,
        telegram_user_id: int,
        paused: bool,
    ) -> bool:
        """Persist an owner-scoped pause/resume transition idempotently."""
        canonical_plan_id = self._canonical_uuid(plan_id, "plan ID")
        self._validate_user_id(telegram_user_id)
        if not isinstance(paused, bool):
            raise ValueError("Task-plan pause state must be a boolean")

        plans = self._ensure_loaded()
        updated: list[TaskPlan] = []
        changed = False
        found = False
        for plan in plans:
            if plan.plan_id != canonical_plan_id or plan.telegram_user_id != telegram_user_id:
                updated.append(plan)
                continue
            found = True
            if plan.paused == paused:
                updated.append(plan)
                continue
            updated.append(replace(plan, paused=paused))
            changed = True
        if not found:
            raise TaskPlanError("Task plan is unavailable")
        if changed:
            self._replace(updated)
        return changed

    def set_all_paused(self, telegram_user_id: int, paused: bool) -> int:
        """Atomically pause or resume every stored plan belonging to one owner."""
        self._validate_user_id(telegram_user_id)
        if not isinstance(paused, bool):
            raise ValueError("Task-plan pause state must be a boolean")

        plans = self._ensure_loaded()
        updated: list[TaskPlan] = []
        changed_count = 0
        for plan in plans:
            if plan.telegram_user_id != telegram_user_id or plan.paused == paused:
                updated.append(plan)
                continue
            updated.append(replace(plan, paused=paused))
            changed_count += 1
        if changed_count:
            self._replace(updated)
        return changed_count
    def _prune_expired(self, now: int) -> None:
        cutoff = now - MAX_TASK_PLAN_AGE_SECONDS
        plans = self._ensure_loaded()
        updated = [plan for plan in plans if plan.created_at >= cutoff]
        if len(updated) != len(plans):
            self._replace(updated)

    def _ensure_loaded(self) -> list[TaskPlan]:
        if self._plans is None:
            self.load()
        return self._plans if self._plans is not None else []

    def _replace(self, plans: list[TaskPlan]) -> None:
        previous = self._plans
        self._plans = plans
        try:
            self._save()
        except TaskPlanError:
            self._plans = previous
            raise

    def _save(self) -> None:
        temporary_path: str | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            payload = {
                "version": 4,
                "plans": [
                    {
                        "plan_id": plan.plan_id,
                        "telegram_user_id": plan.telegram_user_id,
                        "task_type": plan.task_type,
                        "created_at": plan.created_at,
                        "paused": plan.paused,
                        "contract_version": plan.contract_version,
                        "evaluation_scope": plan.evaluation_scope,
                        "planning_status": plan.planning_status,
                        "acceptance_criteria": [
                            {"criterion_id": item.criterion_id, "statement": item.statement}
                            for item in plan.acceptance_criteria
                        ],
                        "verification_steps": list(plan.verification_steps),
                        "goal_satisfaction": plan.goal_satisfaction,
                        "capability_availability": plan.capability_availability,
                        "authorization_checked": plan.authorization_checked,
                        "steps": [
                            {
                                "description": step.description,
                                "task_id": step.task_id,
                                "depends_on": list(step.depends_on),
                                "blocked_by": list(step.blocked_by),
                                "blocked_reason": step.blocked_reason,
                                "step_id": step.step_id,
                                "verifies_criteria": list(step.verifies_criteria),
                                "verification": step.verification,
                            }
                            for step in plan.steps
                        ],
                    }
                    for plan in self._ensure_loaded()
                ],
            }
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            if len(raw.encode("utf-8")) > MAX_TASK_PLAN_BYTES:
                raise TaskPlanError("Telegram task-plan state exceeds its size limit")
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                dir=self._path.parent,
            )
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            else:
                os.chmod(temporary_path, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._path)
            temporary_path = None
            if hasattr(os, "O_DIRECTORY"):
                directory_fd = os.open(self._path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except TaskPlanError:
            raise
        except OSError as exc:
            raise TaskPlanError("Could not persist Telegram task-plan state") from exc
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _validate_acceptance_criteria(value: object, *, allow_empty: bool = True) -> tuple[TaskPlanAcceptanceCriterion, ...]:
        if not isinstance(value, (list, tuple)):
            raise TaskPlanError("Stored acceptance criteria must be a list")
        if len(value) > 12 or (not allow_empty and not value):
            raise TaskPlanError("Acceptance criteria count is invalid")
        normalized: list[TaskPlanAcceptanceCriterion] = []
        seen: set[str] = set()
        for item in value:
            if isinstance(item, TaskPlanAcceptanceCriterion):
                criterion_id, statement = item.criterion_id, item.statement
            elif isinstance(item, dict) and set(item) == {"criterion_id", "statement"}:
                criterion_id, statement = item.get("criterion_id"), item.get("statement")
            else:
                raise TaskPlanError("Acceptance criterion entry is invalid")
            if not isinstance(criterion_id, str) or not re.fullmatch(r"ac_[0-9]{3}", criterion_id):
                raise TaskPlanError("Acceptance criterion ID is invalid")
            if criterion_id in seen:
                raise TaskPlanError("Acceptance criterion IDs cannot be duplicated")
            if not isinstance(statement, str) or not statement.strip() or len(statement.strip()) > 300:
                raise TaskPlanError("Acceptance criterion statement is invalid")
            seen.add(criterion_id)
            normalized.append(TaskPlanAcceptanceCriterion(criterion_id, statement.strip()))
        return tuple(normalized)

    @staticmethod
    def _validate_verification_steps(value: object, *, allow_empty: bool = False) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or len(value) > 12:
            raise TaskPlanError("Verification steps must be a bounded list")
        if not allow_empty and not value:
            raise TaskPlanError("At least one verification step is required")
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item.strip()) > 500:
                raise TaskPlanError("Verification step is invalid")
            normalized.append(item.strip())
        return tuple(normalized)

    @staticmethod
    def _validate_step_contract_fields(
        step_id: object,
        verifies_criteria: object,
        verification: object,
    ) -> tuple[str | None, tuple[str, ...], str | None]:
        if step_id is None and verifies_criteria == [] and verification is None:
            return None, (), None
        if not isinstance(step_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", step_id):
            raise TaskPlanError("Task-plan contract step ID is invalid")
        if not isinstance(verifies_criteria, (list, tuple)) or not 1 <= len(verifies_criteria) <= 12:
            raise TaskPlanError("Task-plan criterion coverage is invalid")
        normalized: list[str] = []
        for criterion_id in verifies_criteria:
            if not isinstance(criterion_id, str) or not re.fullmatch(r"ac_[0-9]{3}", criterion_id):
                raise TaskPlanError("Task-plan criterion reference is invalid")
            if criterion_id in normalized:
                raise TaskPlanError("Task-plan criterion references cannot be duplicated")
            normalized.append(criterion_id)
        if not isinstance(verification, str) or not verification.strip() or len(verification.strip()) > 500:
            raise TaskPlanError("Task-plan step verification is invalid")
        return step_id, tuple(normalized), verification.strip()

    @classmethod
    def _validate_plan_contract(
        cls,
        contract: object,
        descriptions: list[str] | tuple[str, ...],
    ) -> tuple[
        tuple[tuple[int, ...], ...],
        tuple[tuple[str, tuple[str, ...], str], ...],
        dict[str, object],
    ]:
        expected_keys = {
            "contract_version", "planning_status", "evaluation_scope", "goal_satisfaction",
            "goal", "intent", "scope", "assumptions", "constraints", "dependencies",
            "required_capabilities", "capability_availability", "authorization_checked",
            "estimated_complexity", "missing_information", "clarification_questions",
            "research_required", "research_questions", "risk_class", "risk_class_is_authoritative",
            "acceptance_criteria", "verification_steps", "steps", "execution_ready",
            "execution_started", "tools_executed", "tasks_created",
        }
        if not isinstance(contract, dict) or set(contract) != expected_keys:
            raise TaskPlanError("Hub plan contract fields do not match telegram_agent_plan.v1")
        if contract.get("contract_version") != "telegram_agent_plan.v1":
            raise TaskPlanError("Hub plan contract version is unsupported")
        if contract.get("evaluation_scope") != "planning_only" or contract.get("goal_satisfaction") != "not_evaluated":
            raise TaskPlanError("Hub plan contract evaluation scope is invalid")
        if (
            contract.get("capability_availability") != "not_assessed"
            or contract.get("authorization_checked") is not False
            or contract.get("risk_class_is_authoritative") is not False
            or contract.get("execution_ready") is not False
            or contract.get("execution_started") is not False
            or contract.get("tools_executed") is not False
            or type(contract.get("tasks_created")) is not int
            or contract.get("tasks_created") != 0
        ):
            raise TaskPlanError("Hub plan contract execution boundary is invalid")
        if contract.get("planning_status") not in {"needs_clarification", "needs_research", "ready_for_review"}:
            raise TaskPlanError("Hub plan planning status is invalid")
        if contract.get("estimated_complexity") not in {"low", "moderate", "high"}:
            raise TaskPlanError("Hub plan complexity is invalid")
        if not isinstance(contract.get("research_required"), bool):
            raise TaskPlanError("Hub research flag is invalid")
        for key, limit in {
            "goal": 1_000, "intent": 500, "scope": 1_000, "risk_class": 32,
        }.items():
            value = contract.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise TaskPlanError("Hub plan contract text field is invalid")
        for key, limit in {
            "assumptions": 12, "constraints": 20, "dependencies": 20,
            "required_capabilities": 20, "missing_information": 12,
            "clarification_questions": 12, "research_questions": 8,
        }.items():
            values = contract.get(key)
            if not isinstance(values, list) or len(values) > limit or any(
                not isinstance(value, str) or not value.strip() or len(value) > 500
                for value in values
            ):
                raise TaskPlanError("Hub plan contract list field is invalid")

        criteria = cls._validate_acceptance_criteria(contract.get("acceptance_criteria"), allow_empty=False)
        criterion_ids = {item.criterion_id for item in criteria}
        verification_steps = cls._validate_verification_steps(contract.get("verification_steps"))
        raw_steps = contract.get("steps")
        if not isinstance(raw_steps, list) or len(raw_steps) != len(descriptions) or not (
            MIN_TASK_PLAN_STEPS <= len(raw_steps) <= MAX_TASK_PLAN_STEPS
        ):
            raise TaskPlanError("Hub plan contract step count is invalid")

        step_ids: list[str] = []
        step_metadata: list[tuple[str, tuple[str, ...], str]] = []
        dependencies: list[list[int]] = []
        covered_criteria: set[str] = set()
        for index, item in enumerate(raw_steps):
            if not isinstance(item, dict) or set(item) != {
                "step_id", "description", "depends_on", "required_capabilities",
                "verifies_criteria", "verification",
            }:
                raise TaskPlanError("Hub plan contract step shape is invalid")
            description = cls._validate_description(item.get("description"))
            if description != descriptions[index]:
                raise TaskPlanError("Hub plan contract descriptions do not match the legacy step list")
            step_id, coverage, verification = cls._validate_step_contract_fields(
                item.get("step_id"), item.get("verifies_criteria"), item.get("verification")
            )
            if step_id is None or step_id in step_ids:
                raise TaskPlanError("Hub plan step IDs are missing or duplicated")
            step_ids.append(step_id)
            if any(criterion_id not in criterion_ids for criterion_id in coverage):
                raise TaskPlanError("Hub plan step references an unknown acceptance criterion")
            covered_criteria.update(coverage)
            raw_dependencies = item.get("depends_on")
            if not isinstance(raw_dependencies, list):
                raise TaskPlanError("Hub plan step dependencies are invalid")
            dependency_indexes: list[int] = []
            for dependency_id in raw_dependencies:
                if not isinstance(dependency_id, str) or dependency_id not in step_ids[:index]:
                    raise TaskPlanError("Hub plan dependencies must reference earlier steps")
                dependency_indexes.append(step_ids.index(dependency_id))
            if len(set(dependency_indexes)) != len(dependency_indexes):
                raise TaskPlanError("Hub plan dependencies cannot be duplicated")
            dependencies.append(dependency_indexes)
            capabilities = item.get("required_capabilities")
            if not isinstance(capabilities, list) or len(capabilities) > 20 or any(
                not isinstance(capability, str) or not capability.strip() or len(capability) > 80
                for capability in capabilities
            ):
                raise TaskPlanError("Hub plan step capabilities are invalid")
            step_metadata.append((step_id, coverage, verification))

        if covered_criteria != criterion_ids:
            raise TaskPlanError("Hub plan does not map every acceptance criterion to a step")
        normalized_dependencies = cls._normalize_dependencies(dependencies, len(descriptions))
        metadata: dict[str, object] = {
            "contract_version": "telegram_agent_plan.v1",
            "evaluation_scope": "planning_only",
            "planning_status": contract["planning_status"],
            "acceptance_criteria": criteria,
            "verification_steps": verification_steps,
            "goal_satisfaction": "not_evaluated",
            "capability_availability": "not_assessed",
            "authorization_checked": False,
        }
        return normalized_dependencies, tuple(step_metadata), metadata

    @classmethod
    def _validate_stored_plan_contract(
        cls,
        *,
        contract_version: object,
        evaluation_scope: object,
        planning_status: object,
        acceptance_criteria: tuple[TaskPlanAcceptanceCriterion, ...],
        verification_steps: tuple[str, ...],
        goal_satisfaction: object,
        capability_availability: object,
        authorization_checked: object,
        steps: tuple[TaskPlanStep, ...],
    ) -> None:
        if goal_satisfaction != "not_evaluated" or capability_availability != "not_assessed" or authorization_checked is not False:
            raise TaskPlanError("Stored task-plan execution boundary is invalid")
        if contract_version is None:
            if (
                evaluation_scope is not None or planning_status is not None
                or acceptance_criteria or verification_steps
                or any(step.step_id is not None or step.verifies_criteria or step.verification is not None for step in steps)
            ):
                raise TaskPlanError("Stored task-plan contract metadata is incomplete")
            return
        if (
            contract_version != "telegram_agent_plan.v1"
            or evaluation_scope != "planning_only"
            or planning_status not in {"needs_clarification", "needs_research", "ready_for_review"}
            or not acceptance_criteria
            or not verification_steps
        ):
            raise TaskPlanError("Stored task-plan contract metadata is invalid")
        criterion_ids = {item.criterion_id for item in acceptance_criteria}
        seen_steps: set[str] = set()
        covered: set[str] = set()
        for step in steps:
            if step.step_id is None or step.step_id in seen_steps or step.verification is None:
                raise TaskPlanError("Stored contract step metadata is invalid")
            seen_steps.add(step.step_id)
            if not step.verifies_criteria or any(item not in criterion_ids for item in step.verifies_criteria):
                raise TaskPlanError("Stored contract criterion mapping is invalid")
            covered.update(step.verifies_criteria)
        if covered != criterion_ids:
            raise TaskPlanError("Stored plan does not cover every acceptance criterion")

    @classmethod
    def _normalize_dependencies(
        cls,
        dependencies: list[list[int]] | tuple[tuple[int, ...], ...] | None,
        step_count: int,
    ) -> tuple[tuple[int, ...], ...]:
        if dependencies is None:
            return tuple(() if index == 0 else (index - 1,) for index in range(step_count))
        if not isinstance(dependencies, (list, tuple)) or len(dependencies) != step_count:
            raise TaskPlanError("Task-plan dependency groups must match the step count")
        return tuple(
            cls._validate_dependency_group(group, index)
            for index, group in enumerate(dependencies)
        )

    @staticmethod
    def _validate_dependency_group(value: object, step_index: int) -> tuple[int, ...]:
        if not isinstance(value, (list, tuple)):
            raise TaskPlanError("Task-plan dependencies must be lists of earlier step indexes")
        normalized: list[int] = []
        seen: set[int] = set()
        for dependency in value:
            if (
                isinstance(dependency, bool)
                or not isinstance(dependency, int)
                or dependency < 0
                or dependency >= step_index
            ):
                raise TaskPlanError("Task-plan dependencies must point to earlier steps")
            if dependency in seen:
                raise TaskPlanError("Task-plan dependencies cannot be duplicated")
            seen.add(dependency)
            normalized.append(dependency)
        return tuple(sorted(normalized))

    @staticmethod
    def _validate_blocked_by(value: object, dependencies: tuple[int, ...]) -> tuple[int, ...]:
        if not isinstance(value, (list, tuple)):
            raise TaskPlanError("Blocked dependency indexes must be a list")
        normalized: list[int] = []
        seen: set[int] = set()
        for blocker in value:
            if (
                isinstance(blocker, bool)
                or not isinstance(blocker, int)
                or blocker not in dependencies
                or blocker in seen
            ):
                raise TaskPlanError("Blocked dependency indexes are invalid")
            seen.add(blocker)
            normalized.append(blocker)
        return tuple(sorted(normalized))

    @staticmethod
    def _validate_blocked_reason(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or value not in {
            "dependency_failed",
            "dependency_blocked",
            "hub_policy_denied",
        }:
            raise TaskPlanError("Task-plan blocked reason is invalid")
        return value

    @staticmethod
    def _validate_user_id(value: object) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("Telegram user ID must be a positive integer")

    @staticmethod
    def _validate_timestamp(value: object) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Task-plan timestamp must be a non-negative integer")

    @staticmethod
    def _validate_task_type(value: object) -> None:
        if (
            not isinstance(value, str)
            or len(value) > MAX_TASK_TYPE_LENGTH
            or not _TASK_TYPE_PATTERN.fullmatch(value)
        ):
            raise ValueError("Task type is invalid")

    @staticmethod
    def _validate_description(value: object) -> str:
        if not isinstance(value, str):
            raise TaskPlanError("Task-plan description must be text")
        normalized = value.strip()
        if not normalized or len(normalized) > MAX_TASK_STEP_LENGTH:
            raise TaskPlanError("Task-plan descriptions must contain 1–400 characters")
        return normalized

    @staticmethod
    def _canonical_uuid(value: object, label: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"Task-plan {label} must be a canonical UUID")
        try:
            canonical = str(UUID(value))
        except ValueError as exc:
            raise ValueError(f"Task-plan {label} must be a canonical UUID") from exc
        if canonical != value:
            raise ValueError(f"Task-plan {label} must be a canonical UUID")
        return canonical
