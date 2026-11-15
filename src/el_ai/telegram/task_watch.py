"""Durable, owner-scoped Telegram task watches."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


TASK_WATCH_STATUSES = frozenset({
    "queued", "running", "waiting_approval", "succeeded",
    "failed", "cancelled", "rejected",
})
TERMINAL_TASK_STATUSES = frozenset({"succeeded", "failed", "cancelled", "rejected"})
MAX_TASK_WATCHES = 20
MAX_TASK_WATCH_BYTES = 16 * 1024


class TaskWatchError(RuntimeError):
    """Raised when persisted task-watch state is invalid or unavailable."""


@dataclass(frozen=True)
class TaskWatch:
    task_id: str
    telegram_user_id: int
    last_status: str


class TaskWatchStore:
    """Persist task IDs and status checkpoints without task contents or credentials."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._watches: list[TaskWatch] | None = None

    def load(self) -> tuple[TaskWatch, ...]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._path, flags)
        except FileNotFoundError:
            self._watches = []
            return ()
        except OSError as exc:
            raise TaskWatchError("Could not safely open Telegram task-watch state") from exc

        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_TASK_WATCH_BYTES:
                raise TaskWatchError("Telegram task-watch state is not a bounded regular file")
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                raw = handle.read(MAX_TASK_WATCH_BYTES + 1)
            if len(raw.encode("utf-8")) > MAX_TASK_WATCH_BYTES:
                raise TaskWatchError("Telegram task-watch state exceeds its size limit")
            payload = json.loads(raw)
        except TaskWatchError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TaskWatchError("Stored Telegram task-watch state is invalid") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)

        if not isinstance(payload, dict) or set(payload) != {"version", "watches"}:
            raise TaskWatchError("Stored Telegram task-watch state is invalid")
        version = payload.get("version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != 1
            or not isinstance(payload.get("watches"), list)
        ):
            raise TaskWatchError("Stored Telegram task-watch state is unsupported")

        watches: list[TaskWatch] = []
        seen: set[tuple[str, int]] = set()
        for item in payload["watches"]:
            if not isinstance(item, dict) or set(item) != {
                "task_id", "telegram_user_id", "last_status"
            }:
                raise TaskWatchError("Stored Telegram task-watch entry is invalid")
            task_id = item.get("task_id")
            user_id = item.get("telegram_user_id")
            status = item.get("last_status")
            if not isinstance(task_id, str):
                raise TaskWatchError("Stored Telegram task-watch ID is invalid")
            try:
                if str(UUID(task_id)) != task_id:
                    raise ValueError("non-canonical task ID")
            except ValueError as exc:
                raise TaskWatchError("Stored Telegram task-watch ID is invalid") from exc
            if (
                isinstance(user_id, bool)
                or not isinstance(user_id, int)
                or user_id <= 0
                or not isinstance(status, str)
                or status not in TASK_WATCH_STATUSES
            ):
                raise TaskWatchError("Stored Telegram task-watch entry is invalid")
            key = (task_id, user_id)
            if key in seen:
                raise TaskWatchError("Stored Telegram task-watch entries are duplicated")
            seen.add(key)
            watches.append(TaskWatch(task_id, user_id, status))

        if len(watches) > MAX_TASK_WATCHES:
            raise TaskWatchError("Stored Telegram task-watch collection exceeds its limit")
        self._watches = watches
        return tuple(watches)

    def list(self) -> tuple[TaskWatch, ...]:
        return tuple(self._ensure_loaded())

    def list_for_user(self, telegram_user_id: int) -> tuple[TaskWatch, ...]:
        self._validate_user_id(telegram_user_id)
        return tuple(
            watch for watch in self._ensure_loaded()
            if watch.telegram_user_id == telegram_user_id
        )

    def add(self, task_id: str, telegram_user_id: int, status: str) -> bool:
        canonical_id = self._canonical_task_id(task_id)
        self._validate_user_id(telegram_user_id)
        if (
            not isinstance(status, str)
            or status not in TASK_WATCH_STATUSES
            or status in TERMINAL_TASK_STATUSES
        ):
            raise ValueError("Only active task statuses can be watched")
        watches = self._ensure_loaded()
        if any(
            item.task_id == canonical_id and item.telegram_user_id == telegram_user_id
            for item in watches
        ):
            return False
        if len(watches) >= MAX_TASK_WATCHES:
            raise TaskWatchError("The Telegram task-watch limit has been reached")
        self._replace(watches + [TaskWatch(canonical_id, telegram_user_id, status)])
        return True

    def remove(self, task_id: str, telegram_user_id: int) -> bool:
        canonical_id = self._canonical_task_id(task_id)
        self._validate_user_id(telegram_user_id)
        watches = self._ensure_loaded()
        updated = [
            item for item in watches
            if not (
                item.task_id == canonical_id
                and item.telegram_user_id == telegram_user_id
            )
        ]
        if len(updated) == len(watches):
            return False
        self._replace(updated)
        return True

    def update_status(self, task_id: str, telegram_user_id: int, status: str) -> bool:
        canonical_id = self._canonical_task_id(task_id)
        self._validate_user_id(telegram_user_id)
        if not isinstance(status, str) or status not in TASK_WATCH_STATUSES:
            raise ValueError("Task status is invalid")
        watches = self._ensure_loaded()
        updated = [
            TaskWatch(item.task_id, item.telegram_user_id, status)
            if item.task_id == canonical_id and item.telegram_user_id == telegram_user_id
            else item
            for item in watches
        ]
        if updated == watches:
            return False
        self._replace(updated)
        return True

    def _ensure_loaded(self) -> list[TaskWatch]:
        if self._watches is None:
            self.load()
        return self._watches if self._watches is not None else []

    @staticmethod
    def _canonical_task_id(task_id: str) -> str:
        if not isinstance(task_id, str):
            raise ValueError("Task ID must be a canonical UUID")
        try:
            canonical = str(UUID(task_id))
        except ValueError as exc:
            raise ValueError("Task ID must be a canonical UUID") from exc
        if canonical != task_id:
            raise ValueError("Task ID must be a canonical UUID")
        return canonical

    @staticmethod
    def _validate_user_id(telegram_user_id: int) -> None:
        if (
            isinstance(telegram_user_id, bool)
            or not isinstance(telegram_user_id, int)
            or telegram_user_id <= 0
        ):
            raise ValueError("Telegram user ID must be a positive integer")

    def _replace(self, watches: list[TaskWatch]) -> None:
        previous = self._watches
        self._watches = watches
        try:
            self._save()
        except TaskWatchError:
            self._watches = previous
            raise

    def _save(self) -> None:
        temporary_path: str | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            payload = {
                "version": 1,
                "watches": [
                    {
                        "task_id": item.task_id,
                        "telegram_user_id": item.telegram_user_id,
                        "last_status": item.last_status,
                    }
                    for item in self._ensure_loaded()
                ],
            }
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
            if len(raw.encode("utf-8")) > MAX_TASK_WATCH_BYTES:
                raise TaskWatchError("Telegram task-watch state exceeds its size limit")
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
        except TaskWatchError:
            raise
        except OSError as exc:
            raise TaskWatchError("Could not persist Telegram task-watch state") from exc
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
