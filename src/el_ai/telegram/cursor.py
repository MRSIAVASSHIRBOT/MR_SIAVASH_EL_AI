"""Crash-safe persistent Telegram polling cursor."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path


class UpdateCursorError(RuntimeError):
    """Raised when the durable Telegram polling cursor cannot be read or written."""


class UpdateCursor:
    """Persist the next Telegram update offset with atomic replacement."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def load(self) -> int | None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._path, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise UpdateCursorError("Could not safely open Telegram update cursor") from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise UpdateCursorError("Telegram update cursor is not a regular file")
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                payload = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise UpdateCursorError("Stored Telegram update cursor is invalid") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if (
            not isinstance(payload, dict)
            or isinstance(payload.get("next_offset"), bool)
            or not isinstance(payload.get("next_offset"), int)
            or payload["next_offset"] < 0
        ):
            raise UpdateCursorError("Stored Telegram update cursor is invalid")
        return payload["next_offset"]

    def save(self, next_offset: int) -> None:
        if isinstance(next_offset, bool) or not isinstance(next_offset, int) or next_offset < 0:
            raise ValueError("Telegram update cursor must be a non-negative integer")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                dir=self._path.parent,
            )
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"next_offset": next_offset}) + "\n")
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
        except OSError as exc:
            raise UpdateCursorError("Could not persist Telegram update cursor") from exc
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
