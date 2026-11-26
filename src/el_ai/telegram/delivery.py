"""Durable, privacy-minimized state for Telegram outbound update delivery."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from el_ai.telegram.api import split_telegram_message

MAX_DELIVERY_CHUNKS = 128
MAX_DELIVERY_BATCHES = 10_000
DELIVERED_RETENTION_SECONDS = 30 * 24 * 60 * 60


class DeliveryStoreError(RuntimeError):
    """The durable outbound-delivery state cannot be trusted."""


def _validate_identity(update_id: int, chat_id: int) -> None:
    if isinstance(update_id, bool) or not isinstance(update_id, int) or update_id < 0:
        raise ValueError("Telegram update ID must be a non-negative integer")
    if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id == 0:
        raise ValueError("Telegram chat ID must be a non-zero integer")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _response_digest(chunk_digests: Sequence[str]) -> str:
    raw = json.dumps(list(chunk_digests), separators=(",", ":")).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


class DeliveryStore:
    """SQLite outbox that stores digests and status, never response contents."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        try:
            if self._path.is_symlink():
                raise DeliveryStoreError("Telegram delivery database must not be a symlink")
            db = sqlite3.connect(self._path, timeout=5, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA busy_timeout = 5000")
            return db
        except DeliveryStoreError:
            raise
        except sqlite3.Error as exc:
            raise DeliveryStoreError("Could not open Telegram delivery database") from exc

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except DeliveryStoreError:
            db.rollback()
            raise
        except sqlite3.Error as exc:
            db.rollback()
            raise DeliveryStoreError("Telegram delivery transaction failed") from exc
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self) -> None:
        """Create the schema and set restrictive file permissions."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if self._path.is_symlink():
                raise DeliveryStoreError("Telegram delivery database must not be a symlink")
            db = self._connect()
            try:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise DeliveryStoreError("Telegram delivery database version is unsupported")
                db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS delivery_batches (
                        update_id INTEGER PRIMARY KEY,
                        chat_id INTEGER NOT NULL,
                        response_digest TEXT NOT NULL,
                        chunk_count INTEGER NOT NULL,
                        state TEXT NOT NULL CHECK (
                            state IN ('pending', 'in_progress', 'sent', 'quarantined')
                        ),
                        reason_code TEXT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS delivery_chunks (
                        update_id INTEGER NOT NULL,
                        chunk_index INTEGER NOT NULL,
                        content_digest TEXT NOT NULL,
                        state TEXT NOT NULL CHECK (
                            state IN ('pending', 'sending', 'sent', 'unknown')
                        ),
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        telegram_message_id INTEGER,
                        updated_at REAL NOT NULL,
                        PRIMARY KEY (update_id, chunk_index),
                        FOREIGN KEY (update_id)
                            REFERENCES delivery_batches(update_id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS idx_delivery_state_updated
                        ON delivery_batches(state, updated_at);
                    """
                )
                db.execute("PRAGMA user_version = 1")
            finally:
                db.close()
            if os.name != "nt":
                os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
            self._initialized = True
        except DeliveryStoreError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise DeliveryStoreError("Telegram delivery database initialization failed") from exc

    def prepare_update(self, update_id: int, chat_id: int, chunks: Sequence[str]) -> str:
        """Create or validate a response batch and return its durable state."""
        if not self._initialized:
            raise DeliveryStoreError("Telegram delivery store is not initialized")
        _validate_identity(update_id, chat_id)
        if isinstance(chunks, (str, bytes)) or not isinstance(chunks, Sequence):
            raise ValueError("Telegram delivery chunks must be a sequence")
        if len(chunks) > MAX_DELIVERY_CHUNKS or any(not isinstance(c, str) for c in chunks):
            raise ValueError("Telegram delivery chunks are invalid or exceed the limit")

        digests = tuple(_digest(chunk) for chunk in chunks)
        response_digest = _response_digest(digests)
        now = time.time()
        with self._transaction() as db:
            db.execute(
                "DELETE FROM delivery_batches WHERE state='sent' AND updated_at < ?",
                (now - DELIVERED_RETENTION_SECONDS,),
            )
            batch = db.execute(
                "SELECT * FROM delivery_batches WHERE update_id=?", (update_id,)
            ).fetchone()
            if batch is None:
                count = db.execute("SELECT COUNT(*) FROM delivery_batches").fetchone()[0]
                if count >= MAX_DELIVERY_BATCHES:
                    raise DeliveryStoreError("Telegram delivery outbox is full")
                state = "sent" if not chunks else "pending"
                db.execute(
                    """
                    INSERT INTO delivery_batches
                    (update_id, chat_id, response_digest, chunk_count, state, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (update_id, chat_id, response_digest, len(chunks), state, now, now),
                )
                db.executemany(
                    """
                    INSERT INTO delivery_chunks
                    (update_id, chunk_index, content_digest, state, updated_at)
                    VALUES (?, ?, ?, 'pending', ?)
                    """,
                    [(update_id, i, digest, now) for i, digest in enumerate(digests)],
                )
                return state

            if (
                batch["chat_id"] != chat_id
                or batch["response_digest"] != response_digest
                or batch["chunk_count"] != len(chunks)
            ):
                db.execute(
                    """
                    UPDATE delivery_batches SET state='quarantined',
                    reason_code='response_changed', updated_at=? WHERE update_id=?
                    """,
                    (now, update_id),
                )
                return "quarantined"

            interrupted = db.execute(
                """
                UPDATE delivery_chunks SET state='unknown', updated_at=?
                WHERE update_id=? AND state='sending'
                """,
                (now, update_id),
            ).rowcount
            unknown = db.execute(
                "SELECT 1 FROM delivery_chunks WHERE update_id=? AND state='unknown' LIMIT 1",
                (update_id,),
            ).fetchone()
            if interrupted or unknown or batch["state"] == "quarantined":
                db.execute(
                    """
                    UPDATE delivery_batches SET state='quarantined',
                    reason_code=COALESCE(reason_code, 'interrupted_or_unknown_send'),
                    updated_at=? WHERE update_id=?
                    """,
                    (now, update_id),
                )
                return "quarantined"
            return str(batch["state"])

    def begin_chunk(self, update_id: int, chunk_index: int) -> bool:
        """Atomically claim a pending chunk; sending/unknown chunks are never replayed."""
        if not self._initialized:
            raise DeliveryStoreError("Telegram delivery store is not initialized")
        now = time.time()
        with self._transaction() as db:
            batch = db.execute(
                "SELECT state FROM delivery_batches WHERE update_id=?", (update_id,)
            ).fetchone()
            if batch is None:
                raise DeliveryStoreError("Telegram delivery batch is missing")
            if batch["state"] in {"sent", "quarantined"}:
                return False
            chunk = db.execute(
                """
                SELECT state FROM delivery_chunks WHERE update_id=? AND chunk_index=?
                """,
                (update_id, chunk_index),
            ).fetchone()
            if chunk is None:
                raise DeliveryStoreError("Telegram delivery chunk is missing")
            if chunk["state"] == "sent":
                return False
            if chunk["state"] in {"sending", "unknown"}:
                db.execute(
                    """
                    UPDATE delivery_chunks SET state='unknown', updated_at=?
                    WHERE update_id=? AND chunk_index=?
                    """,
                    (now, update_id, chunk_index),
                )
                db.execute(
                    """
                    UPDATE delivery_batches SET state='quarantined',
                    reason_code='interrupted_or_unknown_send', updated_at=? WHERE update_id=?
                    """,
                    (now, update_id),
                )
                return False
            updated = db.execute(
                """
                UPDATE delivery_chunks SET state='sending',
                attempt_count=attempt_count+1, updated_at=?
                WHERE update_id=? AND chunk_index=? AND state='pending'
                """,
                (now, update_id, chunk_index),
            )
            if updated.rowcount != 1:
                raise DeliveryStoreError("Telegram delivery chunk could not be claimed")
            db.execute(
                "UPDATE delivery_batches SET state='in_progress', updated_at=? WHERE update_id=?",
                (now, update_id),
            )
            return True

    def mark_sent(self, update_id: int, chunk_index: int, message_id: int | None) -> None:
        """Persist a confirmed Telegram message result."""
        if message_id is not None and (
            isinstance(message_id, bool) or not isinstance(message_id, int) or message_id < 1
        ):
            raise ValueError("Telegram message ID is invalid")
        now = time.time()
        with self._transaction() as db:
            updated = db.execute(
                """
                UPDATE delivery_chunks SET state='sent', telegram_message_id=?, updated_at=?
                WHERE update_id=? AND chunk_index=? AND state='sending'
                """,
                (message_id, now, update_id, chunk_index),
            )
            if updated.rowcount != 1:
                raise DeliveryStoreError("Telegram delivery chunk was not in sending state")
            pending = db.execute(
                "SELECT COUNT(*) FROM delivery_chunks WHERE update_id=? AND state!='sent'",
                (update_id,),
            ).fetchone()[0]
            state = "sent" if pending == 0 else "in_progress"
            db.execute(
                """
                UPDATE delivery_batches SET state=?, reason_code=NULL, updated_at=?
                WHERE update_id=?
                """,
                (state, now, update_id),
            )

    def mark_unknown(self, update_id: int, chunk_index: int) -> None:
        """Quarantine a send whose remote outcome cannot be confirmed."""
        now = time.time()
        with self._transaction() as db:
            updated = db.execute(
                """
                UPDATE delivery_chunks SET state='unknown', updated_at=?
                WHERE update_id=? AND chunk_index=? AND state='sending'
                """,
                (now, update_id, chunk_index),
            )
            if updated.rowcount != 1:
                raise DeliveryStoreError("Telegram delivery chunk could not be quarantined")
            db.execute(
                """
                UPDATE delivery_batches SET state='quarantined',
                reason_code='telegram_outcome_unknown', updated_at=? WHERE update_id=?
                """,
                (now, update_id),
            )

    def batch_state(self, update_id: int) -> str | None:
        if not self._initialized:
            raise DeliveryStoreError("Telegram delivery store is not initialized")
        try:
            with self._read() as db:
                row = db.execute(
                    "SELECT state FROM delivery_batches WHERE update_id=?", (update_id,)
                ).fetchone()
                return str(row["state"]) if row is not None else None
        except sqlite3.Error as exc:
            raise DeliveryStoreError("Could not read Telegram delivery state") from exc

    def chunk_states(self, update_id: int) -> tuple[tuple[str, int | None], ...]:
        if not self._initialized:
            raise DeliveryStoreError("Telegram delivery store is not initialized")
        try:
            with self._read() as db:
                rows = db.execute(
                    """
                    SELECT state, telegram_message_id FROM delivery_chunks
                    WHERE update_id=? ORDER BY chunk_index
                    """,
                    (update_id,),
                ).fetchall()
                return tuple((str(row["state"]), row["telegram_message_id"]) for row in rows)
        except sqlite3.Error as exc:
            raise DeliveryStoreError("Could not read Telegram delivery chunks") from exc


async def send_update_response(
    api: Any,
    store: DeliveryStore,
    *,
    update_id: int,
    chat_id: int,
    response: str,
) -> str:
    """Send once per confirmed chunk; quarantine any ambiguous remote outcome."""
    chunks = split_telegram_message(response)
    state = store.prepare_update(update_id, chat_id, chunks)
    if state == "sent":
        return "already_delivered"
    if state == "quarantined":
        return "quarantined"

    for index, chunk in enumerate(chunks):
        if not store.begin_chunk(update_id, index):
            state = store.batch_state(update_id)
            if state == "sent":
                continue
            return "quarantined"
        try:
            result = await api.send_message(chat_id, chunk)
        except Exception:  # noqa: BLE001 - never replay a send with an unconfirmed outcome
            store.mark_unknown(update_id, index)
            return "quarantined"
        message_id = (
            result.get("message_id")
            if isinstance(result, dict)
            and isinstance(result.get("message_id"), int)
            and not isinstance(result.get("message_id"), bool)
            and result["message_id"] > 0
            else None
        )
        store.mark_sent(update_id, index, message_id)
    return "delivered"
