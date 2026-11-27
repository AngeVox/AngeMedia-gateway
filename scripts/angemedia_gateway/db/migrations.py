"""Versioned SQLite migrations for durable Gateway state."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable

from ..helpers import now_iso
from .assistant_runtime import migrate_assistant_runtime
from .assistant_access import migrate_assistant_access
from .job_submission import migrate_job_submission
from .assistant_actions import migrate_assistant_actions
from .assistant_action_execution import migrate_assistant_action_execution
from .video_job_origins import migrate_video_job_origins
from .legacy_job_reviews import LEGACY_JOB_REVIEWS_VERSION, migrate_legacy_job_reviews

QUEUE_FOUNDATION_VERSION = "queue_foundation_v1"
IMAGE_JOB_GENERATION_VERSION = "image_job_generation_v1"

# 原建库标记与增量迁移共用版本清单，供初始化和只读兼容性盘点使用。
BASE_SCHEMA_VERSIONS = (
    'baseline', 'gateway_api_keys_v1', 'jobs_v1', 'assets_job_id_v1',
    'jobs_request_hash_v1', 'provider_runtime_config_v1', 'assistant_sessions_v1',
)

Migration = tuple[str, Callable[[sqlite3.Connection], None]]
MIGRATION_TABLE_REQUIREMENTS = {'job_submission_identity_v1': ('jobs',),
                                'assistant_actions_v1': ('assistant_sessions', 'admin_users'),
                                'assistant_action_execution_v1': ('assistant_actions', 'assistant_action_revisions', 'jobs'),
                                'video_job_origins_v1': ('jobs', 'assistant_action_binding_key')}
MIGRATION_TABLE_REQUIREMENTS['image_task_receipts_v1'] = ('jobs', 'assistant_action_binding_key')
MIGRATION_TABLE_REQUIREMENTS['image_layer_receipts_v1'] = ('jobs', 'assets')
MIGRATION_TABLE_REQUIREMENTS['pollinations_video_receipts_v1'] = ('jobs', 'assistant_action_binding_key')
MIGRATION_TABLE_REQUIREMENTS[LEGACY_JOB_REVIEWS_VERSION] = ('jobs', 'admin_users')
MIGRATION_TABLE_REQUIREMENTS["provider_connection_evidence_key_v2"] = ("provider_connection_evidence",)


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


def _provider_local_budget_v1(conn: sqlite3.Connection) -> None:
    """本地保护预算由同一SQLite事务维护，不存储上游余额。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS provider_local_budgets ("
        "provider_id TEXT NOT NULL, budget_day TEXT NOT NULL, "
        "used INTEGER NOT NULL CHECK(used >= 0), updated_at TEXT NOT NULL, "
        "PRIMARY KEY(provider_id,budget_day))"
    )


def _provider_connection_evidence_v1(conn: sqlite3.Connection) -> None:
    """每个渠道只保留最新一份连接观察，序号用于拒绝乱序写回。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS provider_connection_evidence ("
        "provider_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
        "fingerprint_key TEXT NOT NULL, fingerprint TEXT NOT NULL, "
        "invalidated INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, "
        "expires_at TEXT NOT NULL, result_json TEXT)"
    )


def _provider_connection_evidence_key_v2(conn: sqlite3.Connection) -> None:
    # 旧观察由同库密钥签名，升级后需重新验证连接。
    conn.execute(
        "UPDATE provider_connection_evidence SET fingerprint_key='', fingerprint='', "
        "revision=revision+1, invalidated=1"
    )


def _image_task_receipts_v1(conn: sqlite3.Connection) -> None:
    """保留每条实际异步提交的来源；清理任务不能擦除发生过的副作用。"""
    conn.execute('''CREATE TABLE image_task_receipts (
        job_id TEXT NOT NULL CHECK(length(job_id)=32), route_index INTEGER NOT NULL CHECK(route_index>=0),
        provider TEXT NOT NULL CHECK(length(provider) BETWEEN 1 AND 64), model TEXT NOT NULL,
        intent_digest TEXT NOT NULL CHECK(length(intent_digest)=64),
        connection_digest TEXT NOT NULL CHECK(length(connection_digest)=64),
        state TEXT NOT NULL CHECK(state IN ('prepared','rejected','uncertain','accepted')),
        task_id TEXT, provider_status TEXT, result_url TEXT, created_at TEXT NOT NULL,
        PRIMARY KEY(job_id,route_index),
        CHECK((state='accepted' AND task_id IS NOT NULL) OR (state!='accepted' AND task_id IS NULL))
    )''')
    conn.execute("CREATE UNIQUE INDEX image_task_one_accepted ON image_task_receipts(job_id) WHERE state='accepted'")
    conn.execute('''CREATE TRIGGER image_task_receipt_no_replace BEFORE INSERT ON image_task_receipts
        WHEN EXISTS(SELECT 1 FROM image_task_receipts WHERE job_id=NEW.job_id AND route_index=NEW.route_index)
        BEGIN SELECT RAISE(ABORT,'image_task_receipt_immutable'); END''')
    conn.execute('''CREATE TRIGGER image_task_receipt_immutable BEFORE UPDATE ON image_task_receipts
        WHEN NEW.job_id IS NOT OLD.job_id OR NEW.route_index IS NOT OLD.route_index
          OR NEW.provider IS NOT OLD.provider OR NEW.model IS NOT OLD.model
          OR NEW.intent_digest IS NOT OLD.intent_digest OR NEW.connection_digest IS NOT OLD.connection_digest
          OR NEW.created_at IS NOT OLD.created_at
          OR (OLD.state='rejected' AND NEW.state!='rejected')
          OR (OLD.state='accepted' AND (NEW.state!='accepted' OR NEW.task_id IS NOT OLD.task_id))
          OR (OLD.state='uncertain' AND NEW.state NOT IN ('uncertain','accepted'))
        BEGIN SELECT RAISE(ABORT,'image_task_receipt_immutable'); END''')
    conn.execute('''CREATE TRIGGER image_task_receipt_retained BEFORE DELETE ON image_task_receipts
        BEGIN SELECT RAISE(ABORT,'image_task_receipt_retained'); END''')


def _image_layer_receipts_v1(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE image_layer_receipts (
        job_id TEXT PRIMARY KEY CHECK(length(job_id)=32),
        intent_digest TEXT NOT NULL CHECK(length(intent_digest)=64),
        state TEXT NOT NULL CHECK(state IN ('prepared','received','published','failed','removed')),
        deadline TEXT NOT NULL,
        result_json TEXT,
        manifest_json TEXT,
        CHECK((state='received')=(result_json IS NOT NULL)),
        CHECK((state='published')=(manifest_json IS NOT NULL))
    )""")


def _pollinations_video_receipts_v1(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE pollinations_video_receipts (
        job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
        intent_digest TEXT NOT NULL CHECK(length(intent_digest)=64),
        connection_digest TEXT NOT NULL CHECK(length(connection_digest)=64),
        state TEXT NOT NULL CHECK(state IN ('prepared','accepted','cached')),
        task_id TEXT CHECK(length(task_id)=64 AND task_id NOT GLOB '*[^0-9a-f]*'),
        retry_after INTEGER NOT NULL DEFAULT 0 CHECK(retry_after BETWEEN 0 AND 60),
        CHECK((state='accepted')=(task_id IS NOT NULL))
    )""")


MIGRATIONS: tuple[Migration, ...] = (
    (QUEUE_FOUNDATION_VERSION, _queue_foundation_v1),
    (IMAGE_JOB_GENERATION_VERSION, _image_job_generation_v1),
    ("provider_local_budget_v1", _provider_local_budget_v1),
    ("provider_connection_evidence_v1", _provider_connection_evidence_v1),
    ("assistant_runtime_v1", migrate_assistant_runtime),
    ("assistant_access_v1", migrate_assistant_access),
    ("job_submission_identity_v1", migrate_job_submission),
    ("assistant_actions_v1", migrate_assistant_actions),
    ("assistant_action_execution_v1", migrate_assistant_action_execution),
    ("video_job_origins_v1", migrate_video_job_origins),
    ("image_task_receipts_v1", _image_task_receipts_v1),
    ("image_layer_receipts_v1", _image_layer_receipts_v1),
    ("pollinations_video_receipts_v1", _pollinations_video_receipts_v1),
    (LEGACY_JOB_REVIEWS_VERSION, migrate_legacy_job_reviews),
    ("provider_connection_evidence_key_v2", _provider_connection_evidence_key_v2),
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
        # 独立迁移入口可能只包含部分表；前置表未建好时不执行，也不记为已应用。
        if any(not _columns(conn, table) for table in MIGRATION_TABLE_REQUIREMENTS.get(version, ())):
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
