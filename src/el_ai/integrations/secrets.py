"""Protected storage for paired Hub credentials."""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID


class SecretStoreError(RuntimeError):
    """Raised when protected credential storage is unavailable or invalid."""


class CredentialStore(Protocol):
    """Minimal interface shared by OS-vault and encrypted-file stores."""

    def save(self, client_id: str, credentials: dict[str, object]) -> None: ...
    def load(self, client_id: str) -> dict[str, object] | None: ...
    def delete(self, client_id: str) -> None: ...


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
        try:
            payload = json.dumps(credentials, separators=(",", ":"), sort_keys=True)
            self._backend.set_password(self.SERVICE_NAME, client_id, payload)
        except Exception:
            failure = SecretStoreError("Could not store Hub credentials securely")
        else:
            failure = None
        if failure is not None:
            raise failure

    def load(self, client_id: str) -> dict[str, object] | None:
        try:
            payload = self._backend.get_password(self.SERVICE_NAME, client_id)
            if payload is None:
                return None
            value = json.loads(payload)
        except Exception:
            failure = SecretStoreError("Could not read Hub credentials securely")
            value = None
        else:
            failure = None
        if failure is not None:
            raise failure
        if not isinstance(value, dict):
            raise SecretStoreError("Stored Hub credentials have an invalid format")
        return value

    def delete(self, client_id: str) -> None:
        try:
            if self._backend.get_password(self.SERVICE_NAME, client_id) is not None:
                self._backend.delete_password(self.SERVICE_NAME, client_id)
        except Exception:
            failure = SecretStoreError("Could not remove Hub credentials")
        else:
            failure = None
        if failure is not None:
            raise failure


class EncryptedFileCredentialStore:
    """AES-256-GCM encrypted credential file for trusted headless deployments.

    The encryption key must be injected as a base64-encoded 32-byte secret by the
    host's secret manager. No plaintext fallback or key generation is performed.
    All operations after opening the credential directory are relative to its
    verified file descriptor, so renaming or replacing its pathname cannot
    redirect credential reads or writes to a different directory.
    """

    SERVICE_NAME = "MR_SIAVASH_EL_AI"
    MAX_PLAINTEXT_BYTES = 16 * 1024
    MAX_FILE_BYTES = 24 * 1024

    def __init__(self, directory: Path, encoded_key: str | None) -> None:
        if os.name != "posix":
            raise SecretStoreError(
                "encrypted_file storage requires a POSIX host; use the OS keyring on Windows"
            )
        if not isinstance(encoded_key, str) or not encoded_key:
            raise SecretStoreError("Encrypted credential storage key is not configured")
        try:
            key = base64.b64decode(encoded_key, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SecretStoreError("Encrypted credential storage key is invalid") from exc
        if len(key) != 32:
            raise SecretStoreError("Encrypted credential storage key must decode to 32 bytes")
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:
            raise SecretStoreError("AES-GCM support is unavailable") from exc
        self._aesgcm = AESGCM(key)
        self._directory = Path(directory).expanduser()

    @staticmethod
    def _client_filename(client_id: str) -> str:
        try:
            return f"{UUID(client_id)}.json"
        except (ValueError, TypeError, AttributeError) as exc:
            raise SecretStoreError("Credential client identity is invalid") from exc

    def _open_directory(self) -> int:
        """Open and pin the private credential directory without following symlinks."""
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None or not hasattr(os, "O_DIRECTORY"):
            raise SecretStoreError("Safe directory-descriptor operations are unavailable")

        try:
            self._directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            flags = os.O_RDONLY | os.O_DIRECTORY | nofollow | getattr(os, "O_CLOEXEC", 0)
            directory_fd = os.open(self._directory, flags)
        except OSError as exc:
            raise SecretStoreError("Credential directory is unavailable or unsafe") from exc

        try:
            info = os.fstat(directory_fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
                raise SecretStoreError("Credential path must be a directory owned by this user")
            os.fchmod(directory_fd, 0o700)
            info = os.fstat(directory_fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise SecretStoreError("Credential directory permissions are not private")
            return directory_fd
        except Exception:
            os.close(directory_fd)
            raise

    def save(self, client_id: str, credentials: dict[str, object]) -> None:
        filename = self._client_filename(client_id)
        try:
            plaintext = json.dumps(
                credentials, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
            if len(plaintext) > self.MAX_PLAINTEXT_BYTES:
                raise SecretStoreError("Credential payload exceeds the supported size")
            nonce = os.urandom(12)
            aad = f"{self.SERVICE_NAME}:{UUID(client_id)}".encode("ascii")
            ciphertext = self._aesgcm.encrypt(nonce, plaintext, aad)
            envelope = json.dumps(
                {
                    "version": 1,
                    "nonce": base64.b64encode(nonce).decode("ascii"),
                    "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError("Could not encrypt Hub credentials securely") from exc

        directory_fd = self._open_directory()
        temporary_name = f".credentials-{secrets.token_hex(16)}.tmp"
        temporary_fd: int | None = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            temporary_fd = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
            os.fchmod(temporary_fd, 0o600)
            with os.fdopen(temporary_fd, "wb") as handle:
                temporary_fd = None
                handle.write(envelope)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(
                temporary_name,
                filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError("Could not store Hub credentials securely") from exc
        finally:
            if temporary_fd is not None:
                try:
                    os.close(temporary_fd)
                except OSError:
                    pass
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass
            os.close(directory_fd)

    def load(self, client_id: str) -> dict[str, object] | None:
        filename = self._client_filename(client_id)
        directory_fd = self._open_directory()
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
            try:
                descriptor = os.open(filename, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                return None
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
                or info.st_size > self.MAX_FILE_BYTES
            ):
                raise SecretStoreError("Encrypted credential file permissions or size are invalid")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = None
                raw = handle.read(self.MAX_FILE_BYTES + 1)
            if len(raw) > self.MAX_FILE_BYTES:
                raise SecretStoreError("Encrypted credential file exceeds the supported size")
            envelope = json.loads(raw.decode("ascii"))
            if not isinstance(envelope, dict) or envelope.get("version") != 1:
                raise SecretStoreError("Encrypted credential file format is invalid")
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            if len(nonce) != 12:
                raise SecretStoreError("Encrypted credential nonce is invalid")
            aad = f"{self.SERVICE_NAME}:{UUID(client_id)}".encode("ascii")
            plaintext = self._aesgcm.decrypt(nonce, ciphertext, aad)
            value = json.loads(plaintext.decode("utf-8"))
            if not isinstance(value, dict):
                raise SecretStoreError("Stored Hub credentials have an invalid format")
            return value
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError("Could not decrypt Hub credentials") from exc
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            os.close(directory_fd)

    def delete(self, client_id: str) -> None:
        filename = self._client_filename(client_id)
        directory_fd = self._open_directory()
        try:
            try:
                info = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
            ):
                raise SecretStoreError("Credential file permissions are not private")
            os.unlink(filename, dir_fd=directory_fd)
            os.fsync(directory_fd)
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError("Could not remove encrypted Hub credentials") from exc
        finally:
            os.close(directory_fd)


def build_credential_store(state_directory: Path) -> CredentialStore:
    """Select an explicitly configured credential backend; never downgrade security."""
    mode = os.getenv("EL_AI_CREDENTIAL_STORE", "keyring").strip().lower()
    if mode == "keyring":
        return KeyringCredentialStore()
    if mode == "encrypted_file":
        return EncryptedFileCredentialStore(
            Path(state_directory) / "credentials",
            os.getenv("EL_AI_CREDENTIAL_ENCRYPTION_KEY"),
        )
    raise SecretStoreError("EL_AI_CREDENTIAL_STORE must be keyring or encrypted_file")
