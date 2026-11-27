"""持久提交键迁移；任务清理不能解除已经消费的生成意图。"""
from __future__ import annotations

import sqlite3


def migrate_job_submission(conn: sqlite3.Connection) -> None:
    # 保留旧入口的内容唯一性；独立动作由持久键守护，不能被同内容索引误合并。
    conn.execute("ALTER TABLE jobs ADD COLUMN dedupe_scope TEXT NOT NULL DEFAULT 'content' "
                 "CHECK(dedupe_scope IN ('content','submission'))")
    conn.execute('DROP INDEX IF EXISTS uq_jobs_active_request_hash')
    conn.execute("CREATE UNIQUE INDEX uq_jobs_active_request_hash ON jobs(kind,request_hash,request_hash_version) "
                 "WHERE request_hash IS NOT NULL AND request_hash_version IS NOT NULL "
                 "AND status IN ('queued','running') AND dedupe_scope='content'")
    conn.execute("""
        CREATE TABLE job_submission_keys (
            namespace TEXT NOT NULL CHECK(length(namespace) BETWEEN 1 AND 48),
            submission_key TEXT NOT NULL CHECK(length(submission_key)=32),
            intent_hash TEXT NOT NULL CHECK(length(intent_hash)=64),
            job_id TEXT NOT NULL CHECK(length(job_id)=32),
            created_at TEXT NOT NULL,
            PRIMARY KEY(namespace,submission_key)
        )
    """)
    # 不引用可清理的job/run/session；只保留随机标识和摘要，不保存正文或凭据。
    conn.execute("""
        CREATE TRIGGER job_submission_key_immutable BEFORE UPDATE ON job_submission_keys
        BEGIN SELECT RAISE(ABORT, 'job submission identity is immutable'); END
    """)
