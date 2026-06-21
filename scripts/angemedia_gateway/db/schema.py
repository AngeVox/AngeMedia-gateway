"""SQLite schema bootstrap and migrations."""
from __future__ import annotations

import sqlite3
from contextlib import closing

from ..helpers import now_iso
from .connection import db_connect
from .migrations import run_migrations


def init_db() -> None:
    with closing(db_connect()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS generations (
                id TEXT PRIMARY KEY,
                media_type TEXT NOT NULL,
                prompt TEXT NOT NULL,
                enhanced_prompt TEXT,
                model TEXT,
                status TEXT NOT NULL,
                result_url TEXT,
                remote_url TEXT,
                local_path TEXT,
                task_id TEXT,
                job_id TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS video_tasks (
                task_id TEXT PRIMARY KEY,
                prompt TEXT,
                model TEXT,
                status TEXT NOT NULL,
                video_url TEXT,
                remote_video_url TEXT,
                local_path TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                original_filename TEXT,
                role TEXT,
                content_type TEXT,
                url TEXT NOT NULL,
                local_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assistant_plans (
                id TEXT PRIMARY KEY,
                original_prompt TEXT NOT NULL,
                media_type TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS custom_providers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider_type TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT,
                default_model TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                status_url TEXT,
                quota_url TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS provider_runtime_configs (
                provider_id TEXT PRIMARY KEY,
                enabled INTEGER CHECK(enabled IN (0, 1)),
                api_key TEXT,
                base_url_override TEXT,
                default_model_override TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_sessions (
                session_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires_at REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_login_attempts (
                attempt_key TEXT PRIMARY KEY,
                fail_count INTEGER NOT NULL,
                locked_until REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                storage_area TEXT NOT NULL CHECK(storage_area IN ('output', 'upload')),
                relative_path TEXT NOT NULL,
                url_path TEXT NOT NULL,
                media_type TEXT NOT NULL CHECK(media_type IN ('image', 'video')),
                source TEXT NOT NULL CHECK(source IN ('generated', 'upload')),
                size INTEGER NOT NULL DEFAULT 0,
                prompt TEXT,
                model TEXT,
                provider TEXT,
                duration_ms INTEGER,
                created_at TEXT NOT NULL,
                job_id TEXT,
                UNIQUE(storage_area, relative_path)
            );
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gateway_api_keys (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                key_prefix TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                note TEXT,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                last_used_ip TEXT,
                revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('image', 'video')),
                status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')),
                provider TEXT,
                model TEXT,
                prompt TEXT,
                input_json TEXT,
                output_json TEXT,
                error_code TEXT,
                error_message TEXT,
                external_task_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                duration_ms INTEGER,
                request_hash TEXT,
                request_hash_version INTEGER
            );
            """
        )
        for version in (
            "baseline",
            "gateway_api_keys_v1",
            "jobs_v1",
            "assets_job_id_v1",
            "jobs_request_hash_v1",
            "provider_runtime_config_v1",
        ):
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                (version, now_iso()),
            )
        ensure_columns(conn)
        run_migrations(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_kind_request_hash_created_at "
            "ON jobs(kind, request_hash, created_at)"
        )


def ensure_columns(conn: sqlite3.Connection) -> None:
    """Add columns used by newer local DB schemas."""

    additions = {
        "generations": {
            "provider": "TEXT",
            "request_model": "TEXT",
            "input_mode": "TEXT",
            "duration_ms": "INTEGER NOT NULL DEFAULT 0",
            "started_at": "TEXT",
            "completed_at": "TEXT",
            "job_id": "TEXT",
        },
        "video_tasks": {
            "provider": "TEXT",
            "duration_ms": "INTEGER NOT NULL DEFAULT 0",
        },
        "custom_providers": {
            "sort_order": "INTEGER NOT NULL DEFAULT 100",
            "last_test_at": "TEXT",
            "last_test_status": "TEXT",
            "last_response_ms": "INTEGER NOT NULL DEFAULT 0",
            "last_error": "TEXT",
        },
        "assets": {
            "job_id": "TEXT",
        },
        "jobs": {
            "request_hash": "TEXT",
            "request_hash_version": "INTEGER",
            "error_category": "TEXT",
            "human_hint": "TEXT",
            "retryable": "INTEGER NOT NULL DEFAULT 0",
            "gateway_stage": "TEXT",
        },
    }
    for table, columns in additions.items():
        existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
