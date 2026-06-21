"""Versioned SQLite migrations for durable Gateway state."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable

from ..helpers import now_iso

QUEUE_FOUNDATION_VERSION = "queue_foundation_v1"
IMAGE_JOB_GENERATION_VERSION = "image_job_generation_v1"

Migration = tuple[str, Callable[[sqlite3.Connection], None]]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _queue_foundation_v1(conn: sqlite3.Connection) -> None:
    additions = {
        "stage": "TEXT NOT NULL DEFAULT 'admitted'",
        "payload_schema_version": "INTEGER NOT NULL DEFAULT 1 CHECK(payload_schema_version > 0)",
        "priority": "INTEGER NOT NULL DEFAULT 0",
        "scheduled_at": "TEXT",
        "next_retry_at": "TEXT",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0)",
        "max_attempts": "INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts > 0)",
        "claim_token": "TEXT",
        "claim_expires_at": "TEXT",
        "worker_kind": "TEXT",
        "provider_status": "TEXT",
        "cancel_requested_at": "TEXT",
        "version": "INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0)",
    }
    for name, ddl in additions.items():
        _add_column(conn, "jobs", name, ddl)

    conn.execute(
        "UPDATE jobs SET stage='finalize' "
        "WHERE status IN ('succeeded','failed','canceled') AND stage='admitted'"
    )
    statements = (
        """
        CREATE TABLE IF NOT EXISTS job_events (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT,
            stage TEXT,
            payload_json TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_job_events_job_created
            ON job_events(job_id, created_at, id)
        """,
        """
        CREATE TABLE IF NOT EXISTS job_attempts (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
            stage TEXT NOT NULL,
            worker_kind TEXT,
            status TEXT NOT NULL DEFAULT 'running'
                CHECK(status IN ('running','succeeded','failed','canceled')),
            started_at TEXT NOT NULL,
            completed_at TEXT,
            retry_at TEXT,
            error_code TEXT,
            error_message TEXT,
            detail_json TEXT,
            UNIQUE(job_id, attempt_number)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_job_attempts_job_attempt
            ON job_attempts(job_id, attempt_number)
        """,
        """
        CREATE TABLE IF NOT EXISTS job_dispatches (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            topic TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','publishing','published','failed','canceled')),
            available_at TEXT NOT NULL,
            published_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
            last_error TEXT,
            claim_token TEXT,
            claim_expires_at TEXT,
            broker_message_id TEXT,
            version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_job_dispatches_pending
            ON job_dispatches(status, available_at, created_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_job_dispatches_job
            ON job_dispatches(job_id, created_at)
        """,
    )
    for statement in statements:
        conn.execute(statement)

    # Old best-effort admission could leave duplicate active rows. Keep the
    # earliest job authoritative before enforcing the permanent invariant.
    duplicates = conn.execute(
        "SELECT kind,request_hash,request_hash_version FROM jobs "
        "WHERE request_hash IS NOT NULL AND request_hash_version IS NOT NULL "
        "AND status IN ('queued','running') "
        "GROUP BY kind,request_hash,request_hash_version HAVING COUNT(*) > 1"
    ).fetchall()
    for kind, request_hash, request_hash_version in duplicates:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE kind=? AND request_hash=? AND request_hash_version=? "
            "AND status IN ('queued','running') ORDER BY created_at,id",
            (kind, request_hash, request_hash_version),
        ).fetchall()
        for row in rows[1:]:
            conn.execute(
                "UPDATE jobs SET status='failed',stage='finalize',updated_at=?,version=version+1 "
                "WHERE id=?",
                (now_iso(), row[0]),
            )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_active_request_hash "
        "ON jobs(kind,request_hash,request_hash_version) "
        "WHERE request_hash IS NOT NULL AND request_hash_version IS NOT NULL "
        "AND status IN ('queued','running')"
    )


def _image_job_generation_v1(conn: sqlite3.Connection) -> None:
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='generations'"
    ).fetchone() is None:
        return
    _add_column(conn, "generations", "job_id", "TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_generations_job_id "
        "ON generations(job_id) WHERE job_id IS NOT NULL"
    )


MIGRATIONS: tuple[Migration, ...] = (
    (QUEUE_FOUNDATION_VERSION, _queue_foundation_v1),
    (IMAGE_JOB_GENERATION_VERSION, _image_job_generation_v1),
)


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply each migration exactly once using a transactional savepoint."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {
        str(row[0]) for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    for version, migration in MIGRATIONS:
        if version in applied:
            continue
        savepoint = "migration_" + "".join(ch if ch.isalnum() else "_" for ch in version)
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)",
                (version, now_iso()),
            )
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
