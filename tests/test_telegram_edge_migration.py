import sqlite3
from pathlib import Path

import pytest


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "cloudflare"
    / "telegram-edge"
    / "migrations"
    / "0001_initial.sql"
)


def test_initial_edge_migration_creates_application_schema_and_enforces_constraints():
    migration_sql = MIGRATION_PATH.read_text(encoding="utf-8")

    with sqlite3.connect(":memory:") as database:
        database.executescript(migration_sql)

        tables = {
            row[0]
            for row in database.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == {
            "bridge_nonces",
            "telegram_jobs",
            "telegram_processed_updates",
            "telegram_sessions",
        }

        indexes = {
            row[0]
            for row in database.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name NOT LIKE 'sqlite_autoindex_%'"
            )
        }
        assert indexes == {
            "idx_bridge_nonces_expires_at",
            "idx_telegram_jobs_pending",
            "idx_telegram_jobs_updated_at",
            "idx_telegram_processed_updates_processed_at",
        }
        assert "_cf_KV" not in tables

        valid_job = (
            "job-1",
            5001,
            123456789,
            123456789,
            '{"ciphertext":"opaque"}',
            "pending",
            0,
            None,
            0,
            100,
            100,
        )
        insert_job = (
            "INSERT INTO telegram_jobs "
            "(job_id, update_id, chat_id, telegram_user_id, payload_ciphertext, "
            "state, lease_until, lease_token_hash, attempts, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        database.execute(insert_job, valid_job)

        with pytest.raises(sqlite3.IntegrityError):
            database.execute(insert_job, ("job-invalid-state", *valid_job[1:5], "unknown", *valid_job[6:]))

        with pytest.raises(sqlite3.IntegrityError):
            database.execute(insert_job, ("job-duplicate-update", *valid_job[1:]))

        database.execute(
            "INSERT INTO telegram_processed_updates "
            "(update_id, state, lease_until, processed_at) "
            "VALUES (?, ?, ?, ?)",
            (5001, "processing", 115, None),
        )
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(
                "INSERT INTO telegram_processed_updates "
                "(update_id, state, lease_until, processed_at) "
                "VALUES (?, ?, ?, ?)",
                (5002, "unknown", 115, None),
            )

        database.execute(
            "INSERT INTO bridge_nonces (nonce_hash, expires_at) VALUES (?, ?)",
            ("nonce-hash", 220),
        )
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(
                "INSERT INTO bridge_nonces (nonce_hash, expires_at) VALUES (?, ?)",
                ("nonce-hash", 221),
            )
