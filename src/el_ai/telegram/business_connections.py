"""Owner-bound Telegram Business connection registry.

This module stores permission snapshots and resolves a minimal customer actor.
It does not send messages, invoke models, access Hub, or create conversation history.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

BUSINESS_CONTRACT_VERSION = "telegram-business-event.v1"
BUSINESS_STORE_VERSION = 1
MAX_BUSINESS_CONNECTIONS = 100
MAX_BUSINESS_STORE_BYTES = 128 * 1024
MAX_TELEGRAM_ID = (1 << 63) - 1
MAX_TELEGRAM_SAFE_INTEGER = (1 << 53) - 1

BUSINESS_RIGHTS = frozenset({
    "can_reply",
    "can_read_messages",
    "can_delete_sent_messages",
    "can_delete_all_messages",
    "can_edit_name",
    "can_edit_bio",
    "can_edit_profile_photo",
    "can_edit_username",
    "can_change_gift_settings",
    "can_view_gifts_and_stars",
    "can_convert_gifts_to_stars",
    "can_transfer_and_upgrade_gifts",
    "can_transfer_stars",
    "can_manage_stories",
})


class BusinessConnectionError(RuntimeError):
    """Invalid or unavailable Business connection state."""


class BusinessConnectionAuthorizationError(BusinessConnectionError):
    """Business connection does not belong to a configured owner."""


@dataclass(frozen=True, slots=True)
class BusinessConnection:
    connection_id: str
    business_user_id: int
    user_chat_id: int
    connected_at: int
    updated_at: int
    last_update_id: int
    is_enabled: bool
    rights: tuple[tuple[str, bool], ...]

    def has_right(self, name: str) -> bool:
        return dict(self.rights).get(name, False)

    @property
    def can_read_messages(self) -> bool:
        return self.has_right("can_read_messages")

    @property
    def can_reply(self) -> bool:
        return self.has_right("can_reply")


@dataclass(frozen=True, slots=True)
class BusinessActorContext:
    contract_version: str
    actor_type: str
    connection_id: str
    business_user_id: int
    customer_user_id: int
    chat_id: int
    message_id: int
    can_reply: bool
    capabilities: tuple[str, ...] = ()


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_int(value: object, maximum: int = MAX_TELEGRAM_ID) -> bool:
    return _is_int(value) and 0 < value <= maximum


def _non_negative_int(value: object, maximum: int = MAX_TELEGRAM_ID) -> bool:
    return _is_int(value) and 0 <= value <= maximum


def _timestamp(value: object, allow_zero: bool = False) -> bool:
    minimum = 0 if allow_zero else 1
    return _is_int(value) and minimum <= value <= MAX_TELEGRAM_ID


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


class BusinessConnectionStore:
    """Bounded owner-bound registry with atomic, private persistence.

    Rights are replaced as a whole on newer connection updates. Missing rights are
    false. Duplicate or out-of-order Telegram update IDs are idempotently ignored.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._connections: dict[str, BusinessConnection] | None = None
        self._lock = threading.RLock()

    def load(self) -> tuple[BusinessConnection, ...]:
        with self._lock:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(self._path, flags)
            except FileNotFoundError:
                self._connections = {}
                return ()
            except OSError as exc:
                raise BusinessConnectionError(
                    "Could not safely open Business connection state"
                ) from exc

            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size > MAX_BUSINESS_STORE_BYTES
                ):
                    raise BusinessConnectionError(
                        "Business connection state is not a bounded regular file"
                    )
                with os.fdopen(descriptor, "rb") as handle:
                    descriptor = -1
                    raw = handle.read(MAX_BUSINESS_STORE_BYTES + 1)
                if len(raw) > MAX_BUSINESS_STORE_BYTES:
                    raise BusinessConnectionError(
                        "Business connection state exceeds its size limit"
                    )
                payload = json.loads(
                    raw.decode("utf-8"),
                    object_pairs_hook=_unique_object,
                )
            except BusinessConnectionError:
                raise
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                raise BusinessConnectionError(
                    "Stored Business connection state is invalid"
                ) from exc
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

            self._connections = self._parse_payload(payload)
            return tuple(sorted(
                self._connections.values(),
                key=lambda item: (item.business_user_id, item.connection_id),
            ))

    def update_from_event(
        self,
        event: object,
        *,
        owner_ids: frozenset[int] | set[int],
        update_id: int,
        now: int | None = None,
    ) -> BusinessConnection:
        """Persist one validated business_connection event owned by a configured user."""
        with self._lock:
            if not _non_negative_int(update_id):
                raise BusinessConnectionError("Telegram update ID is invalid")
            current_time = int(time.time()) if now is None else now
            if not _timestamp(current_time):
                raise BusinessConnectionError("Business connection update time is invalid")
            owners = self._validate_owner_ids(owner_ids)
            normalized = self._normalize_event(event)
            business_user_id = normalized["business_user_id"]
            if business_user_id not in owners:
                raise BusinessConnectionAuthorizationError(
                    "Business connection is not owned by a configured Telegram owner"
                )

            connections = self._ensure_loaded()
            connection_id = normalized["connection_id"]
            previous = connections.get(connection_id)
            if previous is not None:
                if previous.business_user_id != business_user_id:
                    raise BusinessConnectionAuthorizationError(
                        "Stored Business connection owner does not match"
                    )
                if update_id <= previous.last_update_id:
                    return previous
            elif len(connections) >= MAX_BUSINESS_CONNECTIONS:
                raise BusinessConnectionError(
                    "Business connection registry has reached its entry limit"
                )

            connection = BusinessConnection(
                connection_id=connection_id,
                business_user_id=business_user_id,
                user_chat_id=normalized["user_chat_id"],
                connected_at=normalized["connected_at"],
                updated_at=current_time,
                last_update_id=update_id,
                is_enabled=normalized["is_enabled"],
                rights=tuple(sorted(normalized["rights"].items())),
            )
            updated = dict(connections)
            updated[connection_id] = connection
            self._replace(updated)
            return connection

    def get(self, connection_id: str) -> BusinessConnection | None:
        if not isinstance(connection_id, str) or not connection_id:
            return None
        with self._lock:
            return self._ensure_loaded().get(connection_id)

    def list_for_owner(self, owner_user_id: int) -> tuple[BusinessConnection, ...]:
        if not _positive_int(owner_user_id):
            raise ValueError("Owner ID is invalid")
        with self._lock:
            result = [
                item for item in self._ensure_loaded().values()
                if item.business_user_id == owner_user_id
            ]
            return tuple(sorted(
                result,
                key=lambda item: (item.updated_at, item.connection_id),
                reverse=True,
            ))

    def resolve_customer_actor(self, event: object) -> BusinessActorContext | None:
        """Return a minimal actor only for a current enabled and readable connection.

        No capabilities are granted. No command, model, Hub operation, or reply is run.
        A future reply adapter must re-check can_reply immediately before sending.
        """
        if not isinstance(event, dict) or set(event) != {
            "contract_version", "event_type", "connection_id", "message"
        }:
            return None
        if (
            event.get("contract_version") != BUSINESS_CONTRACT_VERSION
            or event.get("event_type") != "business_message"
        ):
            return None
        connection_id = event.get("connection_id")
        message = event.get("message")
        if not isinstance(connection_id, str) or not connection_id or not isinstance(message, dict):
            return None

        allowed_message_fields = {
            "message_id", "date", "chat", "sender", "text", "caption",
            "media_kind", "is_from_business_account",
        }
        if set(message) - allowed_message_fields:
            return None
        chat = message.get("chat")
        sender = message.get("sender")
        message_id = message.get("message_id")
        message_date = message.get("date")
        is_from_business = message.get("is_from_business_account", False)
        if (
            not _positive_int(message_date)
            or not isinstance(chat, dict)
            or set(chat) != {"id", "type"}
            or chat.get("type") != "private"
            or not _positive_int(chat.get("id"), MAX_TELEGRAM_SAFE_INTEGER)
            or not isinstance(sender, dict)
            or not set(sender).issubset({"id", "is_bot"})
            or not _positive_int(sender.get("id"))
            or ("is_bot" in sender and not isinstance(sender["is_bot"], bool))
            or sender.get("is_bot") is True
            or not _positive_int(message_id)
            or not isinstance(is_from_business, bool)
            or is_from_business
        ):
            return None

        with self._lock:
            connection = self._ensure_loaded().get(connection_id)
            if (
                connection is None
                or not connection.is_enabled
                or not connection.can_read_messages
                or sender["id"] == connection.business_user_id
            ):
                return None
            return BusinessActorContext(
                contract_version=BUSINESS_CONTRACT_VERSION,
                actor_type="business_customer",
                connection_id=connection.connection_id,
                business_user_id=connection.business_user_id,
                customer_user_id=sender["id"],
                chat_id=chat["id"],
                message_id=message_id,
                can_reply=connection.can_reply,
                capabilities=(),
            )

    def _ensure_loaded(self) -> dict[str, BusinessConnection]:
        if self._connections is None:
            self.load()
        return self._connections if self._connections is not None else {}

    def _replace(self, connections: dict[str, BusinessConnection]) -> None:
        previous = self._connections
        self._connections = connections
        try:
            self._save()
        except BusinessConnectionError:
            self._connections = previous
            raise

    def _save(self) -> None:
        temporary_path: str | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            payload = {
                "version": BUSINESS_STORE_VERSION,
                "connections": [
                    {
                        "connection_id": item.connection_id,
                        "business_user_id": item.business_user_id,
                        "user_chat_id": item.user_chat_id,
                        "connected_at": item.connected_at,
                        "updated_at": item.updated_at,
                        "last_update_id": item.last_update_id,
                        "is_enabled": item.is_enabled,
                        "rights": dict(item.rights),
                    }
                    for item in sorted(
                        self._ensure_loaded().values(),
                        key=lambda item: item.connection_id,
                    )
                ],
            }
            raw = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ) + "\n"
            encoded = raw.encode("utf-8")
            if len(encoded) > MAX_BUSINESS_STORE_BYTES:
                raise BusinessConnectionError(
                    "Business connection state exceeds its size limit"
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
        except BusinessConnectionError:
            raise
        except OSError as exc:
            raise BusinessConnectionError(
                "Could not persist Business connection state"
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _validate_owner_ids(owner_ids: object) -> frozenset[int]:
        if not isinstance(owner_ids, (set, frozenset)):
            raise BusinessConnectionError("Configured owner IDs are invalid")
        if any(not _positive_int(value) for value in owner_ids):
            raise BusinessConnectionError("Configured owner IDs are invalid")
        return frozenset(owner_ids)

    @staticmethod
    def _normalize_event(value: object) -> dict[str, object]:
        expected = {
            "contract_version", "event_type", "connection_id", "business_user_id",
            "user_chat_id", "connected_at", "is_enabled", "rights",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise BusinessConnectionError("Business connection event has an invalid shape")
        connection_id = value.get("connection_id")
        business_user_id = value.get("business_user_id")
        user_chat_id = value.get("user_chat_id")
        connected_at = value.get("connected_at")
        is_enabled = value.get("is_enabled")
        rights = value.get("rights")
        if (
            value.get("contract_version") != BUSINESS_CONTRACT_VERSION
            or value.get("event_type") != "business_connection"
            or not isinstance(connection_id, str)
            or not connection_id
            or len(connection_id) > 256
            or connection_id != connection_id.strip()
            or any(character.isspace() for character in connection_id)
            or not _positive_int(business_user_id)
            or not _positive_int(user_chat_id, MAX_TELEGRAM_SAFE_INTEGER)
            or not _timestamp(connected_at)
            or not isinstance(is_enabled, bool)
            or not isinstance(rights, dict)
            or set(rights) - BUSINESS_RIGHTS
            or any(not isinstance(enabled, bool) for enabled in rights.values())
        ):
            raise BusinessConnectionError("Business connection event is invalid")
        return {
            "connection_id": connection_id,
            "business_user_id": business_user_id,
            "user_chat_id": user_chat_id,
            "connected_at": connected_at,
            "is_enabled": is_enabled,
            "rights": {key: rights[key] for key in BUSINESS_RIGHTS if key in rights},
        }

    @staticmethod
    def _parse_payload(value: object) -> dict[str, BusinessConnection]:
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "connections"}
            or type(value.get("version")) is not int
            or value.get("version") != BUSINESS_STORE_VERSION
            or not isinstance(value.get("connections"), list)
            or len(value["connections"]) > MAX_BUSINESS_CONNECTIONS
        ):
            raise BusinessConnectionError("Stored Business connection state is unsupported")

        parsed: dict[str, BusinessConnection] = {}
        expected = {
            "connection_id", "business_user_id", "user_chat_id", "connected_at",
            "updated_at", "last_update_id", "is_enabled", "rights",
        }
        for item in value["connections"]:
            if not isinstance(item, dict) or set(item) != expected:
                raise BusinessConnectionError("Stored Business connection entry is invalid")
            connection_id = item["connection_id"]
            rights = item["rights"]
            if (
                not isinstance(connection_id, str)
                or not connection_id
                or len(connection_id) > 256
                or connection_id != connection_id.strip()
                or any(character.isspace() for character in connection_id)
                or connection_id in parsed
                or not _positive_int(item["business_user_id"])
                or not _positive_int(item["user_chat_id"], MAX_TELEGRAM_SAFE_INTEGER)
                or not _timestamp(item["connected_at"])
                or not _timestamp(item["updated_at"], allow_zero=True)
                or not _non_negative_int(item["last_update_id"])
                or not isinstance(item["is_enabled"], bool)
                or not isinstance(rights, dict)
                or set(rights) - BUSINESS_RIGHTS
                or any(not isinstance(enabled, bool) for enabled in rights.values())
            ):
                raise BusinessConnectionError("Stored Business connection entry is invalid")
            parsed[connection_id] = BusinessConnection(
                connection_id=connection_id,
                business_user_id=item["business_user_id"],
                user_chat_id=item["user_chat_id"],
                connected_at=item["connected_at"],
                updated_at=item["updated_at"],
                last_update_id=item["last_update_id"],
                is_enabled=item["is_enabled"],
                rights=tuple(sorted(rights.items())),
            )
        return parsed
