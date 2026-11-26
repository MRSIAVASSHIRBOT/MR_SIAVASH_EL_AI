"""Encrypted, isolated Telegram Business customer conversation storage.

The store has no plaintext fallback. Its AES-256-GCM key must come from the existing
OS credential vault or explicitly configured encrypted-file credential store.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from el_ai.integrations.secrets import CredentialStore, SecretStoreError
from el_ai.telegram.business_connections import (
    BUSINESS_CONTRACT_VERSION,
    BusinessActorContext,
    BusinessConnection,
    BusinessConnectionStore,
)

BUSINESS_SESSION_STORE_VERSION = 1
BUSINESS_SESSION_KEY_CREDENTIAL_ID = "00000000-0000-4000-8000-0000000000b1"
BUSINESS_SESSION_KEY_PURPOSE = "telegram-business-customer-sessions"
BUSINESS_SESSION_RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_BUSINESS_SESSIONS = 100
MAX_BUSINESS_SESSION_MESSAGES = 20
MAX_BUSINESS_MESSAGE_UTF16_UNITS = 4096
MAX_BUSINESS_SESSION_PAYLOAD_BYTES = 192 * 1024
MAX_BUSINESS_SESSION_FILE_BYTES = 20 * 1024 * 1024
_SESSION_KEY_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ALLOWED_ROLES = frozenset({"customer", "assistant"})


class BusinessSessionError(RuntimeError):
    """Raised when encrypted Business conversation storage is invalid or unavailable."""


class BusinessSessionAuthorizationError(BusinessSessionError):
    """Raised when a caller does not hold a valid Business customer actor context."""


@dataclass(frozen=True, slots=True)
class BusinessSessionMessage:
    message_id: int
    role: Literal["customer", "assistant"]
    content: str
    created_at: int


def load_or_create_business_session_key(
    credential_store: CredentialStore,
) -> bytes:
    """Load or create the independent AES-256 key through a protected credential store.

    No local plaintext key file, environment-variable fallback, or implicit key
    regeneration is permitted. Losing the credential makes old ciphertext unreadable.
    """
    if credential_store is None:
        raise BusinessSessionError("A protected credential store is required")

    try:
        stored = credential_store.load(BUSINESS_SESSION_KEY_CREDENTIAL_ID)
        if stored is None:
            key = os.urandom(32)
            credential_store.save(
                BUSINESS_SESSION_KEY_CREDENTIAL_ID,
                {
                    "purpose": BUSINESS_SESSION_KEY_PURPOSE,
                    "version": 1,
                    "key": base64.b64encode(key).decode("ascii"),
                },
            )
            stored = credential_store.load(BUSINESS_SESSION_KEY_CREDENTIAL_ID)
        if (
            not isinstance(stored, dict)
            or set(stored) != {"purpose", "version", "key"}
            or stored.get("purpose") != BUSINESS_SESSION_KEY_PURPOSE
            or type(stored.get("version")) is not int
            or stored.get("version") != 1
            or not isinstance(stored.get("key"), str)
        ):
            raise BusinessSessionError("Stored Business session key metadata is invalid")
        key = base64.b64decode(stored["key"], validate=True)
        if len(key) != 32:
            raise BusinessSessionError("Stored Business session key has an invalid length")
        return key
    except BusinessSessionError:
        raise
    except (SecretStoreError, ValueError, TypeError, OSError) as exc:
        raise BusinessSessionError(
            "Protected Business session key storage is unavailable"
        ) from exc
    except Exception as exc:
        raise BusinessSessionError(
            "Protected Business session key storage is unavailable"
        ) from exc


class BusinessConversationStore:
    """Bounded encrypted session store keyed by connection and customer chat.

    The JSON file contains only HMAC-derived opaque session identifiers, timestamps,
    nonces and AES-GCM ciphertext. Customer IDs, message contents and role metadata
    remain inside the authenticated encrypted payload.
    """

    def __init__(
        self,
        path: Path,
        encryption_key: bytes,
        connection_store: BusinessConnectionStore,
        *,
        retention_seconds: int = BUSINESS_SESSION_RETENTION_SECONDS,
        max_sessions: int = MAX_BUSINESS_SESSIONS,
        max_messages_per_session: int = MAX_BUSINESS_SESSION_MESSAGES,
    ) -> None:
        if not isinstance(encryption_key, bytes) or len(encryption_key) != 32:
            raise BusinessSessionError("Business session AES key must contain 32 bytes")
        if (
            isinstance(retention_seconds, bool)
            or not isinstance(retention_seconds, int)
            or retention_seconds < 60
        ):
            raise ValueError("Business session retention must be at least 60 seconds")
        if (
            isinstance(max_sessions, bool)
            or not isinstance(max_sessions, int)
            or not 1 <= max_sessions <= MAX_BUSINESS_SESSIONS
        ):
            raise ValueError("Business session count limit is invalid")
        if (
            isinstance(max_messages_per_session, bool)
            or not isinstance(max_messages_per_session, int)
            or not 1 <= max_messages_per_session <= MAX_BUSINESS_SESSION_MESSAGES
        ):
            raise ValueError("Business session history limit is invalid")

        if not isinstance(connection_store, BusinessConnectionStore):
            raise TypeError("A BusinessConnectionStore is required")
        self._path = Path(path)
        self._connection_store = connection_store
        self._aesgcm = AESGCM(encryption_key)
        self._index_key = hmac.new(
            encryption_key,
            b"mr-siavash-el-ai:business-session-index:v1",
            hashlib.sha256,
        ).digest()
        self._retention_seconds = retention_seconds
        self._max_sessions = max_sessions
        self._max_messages = max_messages_per_session
        self._sessions: dict[str, dict[str, object]] | None = None
        self._lock = threading.RLock()

    def load(self) -> int:
        """Load and validate the bounded encrypted index; return its session count."""
        with self._lock:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(self._path, flags)
            except FileNotFoundError:
                self._sessions = {}
                return 0
            except OSError as exc:
                raise BusinessSessionError(
                    "Could not safely open the Business session store"
                ) from exc

            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size > MAX_BUSINESS_SESSION_FILE_BYTES
                ):
                    raise BusinessSessionError(
                        "Business session store is not a bounded regular file"
                    )
                if os.name == "posix" and (
                    metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077
                ):
                    raise BusinessSessionError(
                        "Business session store permissions are not private"
                    )
                with os.fdopen(descriptor, "rb") as handle:
                    descriptor = -1
                    raw = handle.read(MAX_BUSINESS_SESSION_FILE_BYTES + 1)
                if len(raw) > MAX_BUSINESS_SESSION_FILE_BYTES:
                    raise BusinessSessionError(
                        "Business session store exceeds its size limit"
                    )
                value = json.loads(
                    raw.decode("utf-8"),
                    object_pairs_hook=_unique_json_object,
                )
                self._sessions = self._validate_index(value)
                return len(self._sessions)
            except BusinessSessionError:
                raise
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                raise BusinessSessionError(
                    "Business session store has an invalid format"
                ) from exc
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

    def append_message(
        self,
        actor: BusinessActorContext,
        *,
        role: Literal["customer", "assistant"],
        message_id: int,
        content: str,
        now: int,
    ) -> bool:
        """Append one bounded text message; duplicate message/role pairs are idempotent.

        Store assistant messages only after Telegram confirms a successful send.
        The caller must re-resolve the connection and can_reply immediately before
        sending; this local store is not the authorization authority.
        """
        identity = self._validate_actor(actor)
        self._assert_current_connection(actor, identity)
        if not isinstance(role, str) or role not in _ALLOWED_ROLES:
            raise ValueError("Business session message role is invalid")
        if role == "assistant":
            self._assert_current_reply_permission(actor, identity)
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or message_id <= 0
        ):
            raise ValueError("Business session message ID is invalid")
        if (
            isinstance(now, bool)
            or not isinstance(now, int)
            or now <= 0
        ):
            raise ValueError("Business session timestamp is invalid")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Business session message must contain text")
        try:
            if len(content.encode("utf-16-le")) // 2 > MAX_BUSINESS_MESSAGE_UTF16_UNITS:
                raise ValueError("Business session message exceeds the UTF-16 limit")
        except UnicodeEncodeError as exc:
            raise ValueError("Business session message contains invalid Unicode") from exc

        with self._lock:
            sessions = self._ensure_loaded()
            session_key = self._session_key(identity["connection_id"], identity["chat_id"])
            record = sessions.get(session_key)
            if record is None:
                if len(sessions) >= self._max_sessions:
                    raise BusinessSessionError("Business session count limit has been reached")
                messages: list[dict[str, object]] = []
            else:
                payload = self._decrypt_record(session_key, record)
                self._assert_identity(payload, identity)
                if now - int(record["updated_at"]) > self._retention_seconds:
                    messages = []
                else:
                    messages = list(payload["messages"])

            if any(
                item["message_id"] == message_id and item["role"] == role
                for item in messages
            ):
                return False

            messages.append(
                {
                    "message_id": message_id,
                    "role": role,
                    "content": content,
                    "created_at": now,
                }
            )
            messages = messages[-self._max_messages :]
            payload = {
                "version": BUSINESS_SESSION_STORE_VERSION,
                **identity,
                "messages": messages,
            }
            updated = dict(sessions)
            updated[session_key] = self._encrypt_record(session_key, payload, updated_at=now)
            self._replace(updated)
            return True

    def read_history(
        self,
        actor: BusinessActorContext,
        *,
        now: int,
    ) -> tuple[BusinessSessionMessage, ...]:
        identity = self._validate_actor(actor)
        self._assert_current_connection(actor, identity)
        if isinstance(now, bool) or not isinstance(now, int) or now <= 0:
            raise ValueError("Business session timestamp is invalid")

        with self._lock:
            sessions = self._ensure_loaded()
            session_key = self._session_key(identity["connection_id"], identity["chat_id"])
            record = sessions.get(session_key)
            if record is None:
                return ()
            if now - int(record["updated_at"]) > self._retention_seconds:
                updated = dict(sessions)
                updated.pop(session_key, None)
                self._replace(updated)
                return ()

            payload = self._decrypt_record(session_key, record)
            self._assert_identity(payload, identity)
            return tuple(
                BusinessSessionMessage(
                    message_id=item["message_id"],
                    role=item["role"],
                    content=item["content"],
                    created_at=item["created_at"],
                )
                for item in payload["messages"]
            )

    def delete_session(self, actor: BusinessActorContext) -> bool:
        identity = self._validate_actor(actor)
        # Deletion remains available after a Business connection is disabled or
        # read permission is revoked, provided its owner binding is still known.
        self._assert_owner_binding(actor, identity)
        with self._lock:
            sessions = self._ensure_loaded()
            session_key = self._session_key(identity["connection_id"], identity["chat_id"])
            record = sessions.get(session_key)
            if record is None:
                return False
            payload = self._decrypt_record(session_key, record)
            self._assert_identity(payload, identity)
            updated = dict(sessions)
            updated.pop(session_key, None)
            self._replace(updated)
            return True

    def prune_expired(self, *, now: int) -> int:
        if isinstance(now, bool) or not isinstance(now, int) or now <= 0:
            raise ValueError("Business session timestamp is invalid")
        with self._lock:
            sessions = self._ensure_loaded()
            updated = {
                session_key: record
                for session_key, record in sessions.items()
                if now - int(record["updated_at"]) <= self._retention_seconds
            }
            removed = len(sessions) - len(updated)
            if removed:
                self._replace(updated)
            return removed

    def _ensure_loaded(self) -> dict[str, dict[str, object]]:
        if self._sessions is None:
            self.load()
        return self._sessions if self._sessions is not None else {}

    def _replace(self, sessions: dict[str, dict[str, object]]) -> None:
        previous = self._sessions
        self._sessions = sessions
        try:
            self._save()
        except Exception:
            self._sessions = previous
            raise

    def _ensure_private_directory(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name == "posix":
                metadata = os.lstat(self._path.parent)
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or stat.S_ISLNK(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                ):
                    raise BusinessSessionError(
                        "Business session directory must be owned by the current user"
                    )
                os.chmod(self._path.parent, 0o700)
                metadata = os.stat(self._path.parent, follow_symlinks=False)
                if metadata.st_mode & 0o077:
                    raise BusinessSessionError(
                        "Business session directory permissions are not private"
                    )
        except BusinessSessionError:
            raise
        except OSError as exc:
            raise BusinessSessionError(
                "Business session directory is unavailable or unsafe"
            ) from exc

    def _save(self) -> None:
        temporary_path: str | None = None
        try:
            self._ensure_private_directory()
            payload = {
                "version": BUSINESS_SESSION_STORE_VERSION,
                "sessions": self._ensure_loaded(),
            }
            raw = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ) + "\n"
            encoded = raw.encode("utf-8")
            if len(encoded) > MAX_BUSINESS_SESSION_FILE_BYTES:
                raise BusinessSessionError(
                    "Business session store exceeds its total size limit"
                )
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                dir=self._path.parent,
            )
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            else:
                os.chmod(temporary_path, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
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
        except BusinessSessionError:
            raise
        except OSError as exc:
            raise BusinessSessionError(
                "Could not persist encrypted Business sessions"
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    def _session_key(self, connection_id: str, chat_id: int) -> str:
        material = (
            f"{BUSINESS_CONTRACT_VERSION}\n{connection_id}\n{chat_id}"
        ).encode("utf-8")
        return hmac.new(self._index_key, material, hashlib.sha256).hexdigest()

    def _encrypt_record(
        self,
        session_key: str,
        payload: dict[str, object],
        *,
        updated_at: int,
    ) -> dict[str, object]:
        plaintext = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(plaintext) > MAX_BUSINESS_SESSION_PAYLOAD_BYTES:
            raise BusinessSessionError("Business session payload exceeds its size limit")
        nonce = os.urandom(12)
        aad = f"mr-siavash-business-session:v1:{session_key}".encode("ascii")
        ciphertext = self._aesgcm.encrypt(nonce, plaintext, aad)
        return {
            "updated_at": updated_at,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }

    def _decrypt_record(
        self,
        session_key: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        try:
            nonce = base64.b64decode(record["nonce"], validate=True)
            ciphertext = base64.b64decode(record["ciphertext"], validate=True)
            if len(nonce) != 12 or not ciphertext:
                raise ValueError("Invalid AES-GCM envelope")
            aad = f"mr-siavash-business-session:v1:{session_key}".encode("ascii")
            plaintext = self._aesgcm.decrypt(nonce, ciphertext, aad)
            payload = json.loads(
                plaintext.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
            )
        except Exception as exc:
            raise BusinessSessionError(
                "Business session ciphertext could not be authenticated"
            ) from exc
        self._validate_payload(payload)
        return payload

    def _validate_payload(self, value: object) -> None:
        expected = {
            "version",
            "contract_version",
            "connection_id",
            "business_user_id",
            "chat_id",
            "customer_user_id",
            "messages",
        }
        if (
            not isinstance(value, dict)
            or set(value) != expected
            or type(value.get("version")) is not int
            or value.get("version") != BUSINESS_SESSION_STORE_VERSION
            or value.get("contract_version") != BUSINESS_CONTRACT_VERSION
            or not isinstance(value.get("connection_id"), str)
            or not value["connection_id"]
            or not _positive_int(value.get("business_user_id"))
            or not _positive_int(value.get("chat_id"))
            or not _positive_int(value.get("customer_user_id"))
            or not isinstance(value.get("messages"), list)
            or len(value["messages"]) > self._max_messages
        ):
            raise BusinessSessionError("Decrypted Business session payload is invalid")
        for item in value["messages"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"message_id", "role", "content", "created_at"}
                or not _positive_int(item.get("message_id"))
                or not isinstance(item.get("role"), str)
                or item["role"] not in _ALLOWED_ROLES
                or not isinstance(item.get("content"), str)
                or not item["content"]
                or not _positive_int(item.get("created_at"))
            ):
                raise BusinessSessionError("Decrypted Business session message is invalid")
            try:
                text_units = len(item["content"].encode("utf-16-le")) // 2
            except UnicodeEncodeError as exc:
                raise BusinessSessionError(
                    "Decrypted Business session message contains invalid Unicode"
                ) from exc
            if text_units > MAX_BUSINESS_MESSAGE_UTF16_UNITS:
                raise BusinessSessionError("Decrypted Business session message is too long")

    def _assert_identity(
        self,
        payload: dict[str, object],
        identity: dict[str, object],
    ) -> None:
        for key in (
            "connection_id",
            "business_user_id",
            "chat_id",
            "customer_user_id",
        ):
            if payload.get(key) != identity[key]:
                raise BusinessSessionAuthorizationError(
                    "Business session identity does not match the current actor"
                )

    def _assert_owner_binding(
        self,
        actor: BusinessActorContext,
        identity: dict[str, object],
    ):
        connection = self._connection_store.get(actor.connection_id)
        if (
            connection is None
            or connection.business_user_id != identity["business_user_id"]
            or actor.customer_user_id == connection.business_user_id
        ):
            raise BusinessSessionAuthorizationError(
                "Business session is not bound to the stored owner identity"
            )
        return connection

    def _assert_current_connection(
        self,
        actor: BusinessActorContext,
        identity: dict[str, object],
    ):
        connection = self._assert_owner_binding(actor, identity)
        if not connection.is_enabled or not connection.can_read_messages:
            raise BusinessSessionAuthorizationError(
                "Business connection or read permission is no longer valid"
            )
        return connection

    def _assert_current_reply_permission(
        self,
        actor: BusinessActorContext,
        identity: dict[str, object],
    ) -> None:
        if not actor.can_reply:
            raise BusinessSessionAuthorizationError(
                "Business actor context does not permit replies"
            )
        connection = self._assert_current_connection(actor, identity)
        if not connection.can_reply:
            raise BusinessSessionAuthorizationError(
                "Business connection does not currently allow replies"
            )

    @staticmethod
    def _validate_actor(actor: BusinessActorContext) -> dict[str, object]:
        if (
            not isinstance(actor, BusinessActorContext)
            or actor.contract_version != BUSINESS_CONTRACT_VERSION
            or actor.actor_type != "business_customer"
            or not isinstance(actor.connection_id, str)
            or not actor.connection_id
            or not _positive_int(actor.business_user_id)
            or not _positive_int(actor.customer_user_id)
            or not _positive_int(actor.chat_id)
            or not _positive_int(actor.message_id)
            or actor.capabilities != ()
            or not isinstance(actor.can_reply, bool)
        ):
            raise BusinessSessionAuthorizationError(
                "A valid Business customer actor context is required"
            )
        return {
            "contract_version": BUSINESS_CONTRACT_VERSION,
            "connection_id": actor.connection_id,
            "business_user_id": actor.business_user_id,
            "chat_id": actor.chat_id,
            "customer_user_id": actor.customer_user_id,
        }

    @staticmethod
    def _validate_index(value: object) -> dict[str, dict[str, object]]:
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "sessions"}
            or type(value.get("version")) is not int
            or value.get("version") != BUSINESS_SESSION_STORE_VERSION
            or not isinstance(value.get("sessions"), dict)
            or len(value["sessions"]) > MAX_BUSINESS_SESSIONS
        ):
            raise BusinessSessionError("Business session index has an unsupported format")
        sessions: dict[str, dict[str, object]] = {}
        for session_key, record in value["sessions"].items():
            if (
                not isinstance(session_key, str)
                or not _SESSION_KEY_PATTERN.fullmatch(session_key)
                or not isinstance(record, dict)
                or set(record) != {"updated_at", "nonce", "ciphertext"}
                or not _positive_int(record.get("updated_at"))
                or not isinstance(record.get("nonce"), str)
                or not isinstance(record.get("ciphertext"), str)
            ):
                raise BusinessSessionError("Business session index entry is invalid")
            sessions[session_key] = dict(record)
        return sessions


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result
