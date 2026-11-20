"""Private, bounded Telegram task-plan drafts with resumable idempotent submission."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import time
from dataclasses import dataclass
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
class TaskPlanStep:
    description: str
    task_id: str | None = None


@dataclass(frozen=True)
class TaskPlan:
    plan_id: str
    telegram_user_id: int
    task_type: str
    created_at: int
    steps: tuple[TaskPlanStep, ...]

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
            or payload.get("version") != 1
            or not isinstance(payload.get("plans"), list)
        ):
            raise TaskPlanError("Stored Telegram task-plan state is unsupported")

        plans: list[TaskPlan] = []
        seen: set[str] = set()
        for item in payload["plans"]:
            if not isinstance(item, dict) or set(item) != {
                "plan_id", "telegram_user_id", "task_type", "created_at", "steps"
            }:
                raise TaskPlanError("Stored Telegram task-plan entry is invalid")
            plan_id = self._canonical_uuid(item.get("plan_id"), "plan ID")
            user_id = item.get("telegram_user_id")
            task_type = item.get("task_type")
            created_at = item.get("created_at")
            raw_steps = item.get("steps")
            self._validate_user_id(user_id)
            self._validate_task_type(task_type)
            self._validate_timestamp(created_at)
            if not isinstance(raw_steps, list) or not (
                MIN_TASK_PLAN_STEPS <= len(raw_steps) <= MAX_TASK_PLAN_STEPS
            ):
                raise TaskPlanError("Stored Telegram task-plan step count is invalid")
            steps: list[TaskPlanStep] = []
            for raw_step in raw_steps:
                if not isinstance(raw_step, dict) or set(raw_step) != {
                    "description", "task_id"
                }:
                    raise TaskPlanError("Stored Telegram task-plan step is invalid")
                description = self._validate_description(raw_step.get("description"))
                task_id = raw_step.get("task_id")
                if task_id is not None:
                    task_id = self._canonical_uuid(task_id, "task ID")
                steps.append(TaskPlanStep(description, task_id))
            if plan_id in seen:
                raise TaskPlanError("Stored Telegram task plans are duplicated")
            seen.add(plan_id)
            plans.append(TaskPlan(
                plan_id=plan_id,
                telegram_user_id=user_id,
                task_type=task_type,
                created_at=created_at,
                steps=tuple(steps),
            ))

        if len(plans) > MAX_TASK_PLANS:
            raise TaskPlanError("Stored Telegram task-plan collection exceeds its limit")
        self._plans = plans
        return tuple(plans)

    def create(
        self,
        telegram_user_id: int,
        task_type: str,
        descriptions: list[str] | tuple[str, ...],
        *,
        now: int | None = None,
    ) -> TaskPlan:
        self._validate_user_id(telegram_user_id)
        self._validate_task_type(task_type)
        if not isinstance(descriptions, (list, tuple)) or not (
            MIN_TASK_PLAN_STEPS <= len(descriptions) <= MAX_TASK_PLAN_STEPS
        ):
            raise TaskPlanError(
                f"A task plan must contain {MIN_TASK_PLAN_STEPS}–{MAX_TASK_PLAN_STEPS} steps"
            )
        steps = tuple(
            TaskPlanStep(self._validate_description(description))
            for description in descriptions
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
        )
        self._replace(plans + [plan])
        return plan

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
            existing = steps[step_index].task_id
            if existing == canonical_task_id:
                updated.append(plan)
                continue
            if existing is not None:
                raise TaskPlanError("A different task is already recorded for this plan step")
            steps[step_index] = TaskPlanStep(steps[step_index].description, canonical_task_id)
            updated.append(TaskPlan(
                plan.plan_id,
                plan.telegram_user_id,
                plan.task_type,
                plan.created_at,
                tuple(steps),
            ))
            changed = True
        if not found:
            raise TaskPlanError("Task plan is unavailable")
        if changed:
            self._replace(updated)
        return changed

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
                "version": 1,
                "plans": [
                    {
                        "plan_id": plan.plan_id,
                        "telegram_user_id": plan.telegram_user_id,
                        "task_type": plan.task_type,
                        "created_at": plan.created_at,
                        "steps": [
                            {"description": step.description, "task_id": step.task_id}
                            for step in plan.steps
                        ],
                    }
                    for plan in self._ensure_loaded()
                ],
            }
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\\n"
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
            raise ValueError("Task-plan description must be text")
        normalized = value.strip()
        if not normalized or len(normalized) > MAX_TASK_STEP_LENGTH:
            raise ValueError("Task-plan descriptions must contain 1–400 characters")
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
