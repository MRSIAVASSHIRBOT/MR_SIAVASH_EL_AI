"""Stable, non-secret identity for this local bot installation."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4


class InstanceIdentityError(RuntimeError):
    """Raised when the local installation identity cannot be read safely."""


def load_or_create_client_id(state_dir: Path) -> str:
    state_dir.mkdir(parents=True, exist_ok=True)
    identity_path = state_dir / "client-id"
    if identity_path.exists():
        try:
            return str(UUID(identity_path.read_text(encoding="ascii").strip()))
        except (OSError, ValueError) as exc:
            raise InstanceIdentityError("Stored EL_AI client identity is invalid") from exc

    candidate = str(uuid4())
    try:
        descriptor = os.open(identity_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            return str(UUID(identity_path.read_text(encoding="ascii").strip()))
        except (OSError, ValueError) as exc:
            raise InstanceIdentityError("Stored EL_AI client identity is invalid") from exc
    except OSError as exc:
        raise InstanceIdentityError("Could not persist EL_AI client identity") from exc

    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(candidate + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise InstanceIdentityError("Could not persist EL_AI client identity") from exc
    return candidate
