"""OS-backed storage for paired Hub credentials."""

from __future__ import annotations

import json
from typing import Any


class SecretStoreError(RuntimeError):
    """Raised when a protected operating-system credential store is unavailable."""


class KeyringCredentialStore:
    SERVICE_NAME = "MR_SIAVASH_EL_AI"
    _TRUSTED_BACKENDS = (
        "keyring.backends.Windows",
        "keyring.backends.macOS",
        "keyring.backends.SecretService",
        "keyring.backends.kwallet",
        "keyring.backends.libsecret",
    )

    def __init__(self, backend: Any | None = None) -> None:
        if backend is None:
            failure = None
            try:
                import keyring
                backend = keyring.get_keyring()
            except Exception:
                failure = SecretStoreError("Operating-system credential storage is unavailable")
            if failure is not None:
                raise failure
            module = backend.__class__.__module__
            if getattr(backend, "priority", 0) <= 0 or not module.startswith(self._TRUSTED_BACKENDS):
                raise SecretStoreError("No trusted operating-system credential backend is configured")
        self._backend = backend

    def save(self, client_id: str, credentials: dict[str, object]) -> None:
        failure = None
        try:
            payload = json.dumps(credentials, separators=(",", ":"), sort_keys=True)
            self._backend.set_password(self.SERVICE_NAME, client_id, payload)
        except Exception:
            failure = SecretStoreError("Could not store Hub credentials securely")
        if failure is not None:
            raise failure

    def load(self, client_id: str) -> dict[str, object] | None:
        failure = None
        value = None
        try:
            payload = self._backend.get_password(self.SERVICE_NAME, client_id)
            if payload is None:
                return None
            value = json.loads(payload)
        except Exception:
            failure = SecretStoreError("Could not read Hub credentials securely")
        if failure is not None:
            raise failure
        if not isinstance(value, dict):
            raise SecretStoreError("Stored Hub credentials have an invalid format")
        return value

    def delete(self, client_id: str) -> None:
        failure = None
        try:
            if self._backend.get_password(self.SERVICE_NAME, client_id) is None:
                return
            self._backend.delete_password(self.SERVICE_NAME, client_id)
        except Exception:
            failure = SecretStoreError("Could not remove Hub credentials")
        if failure is not None:
            raise failure
