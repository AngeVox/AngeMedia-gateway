"""队列视频执行来源收据；不回填历史，任务清理不能擦除已发生的尝试。"""
import sqlite3


def migrate_video_job_origins(conn: sqlite3.Connection) -> None:
    conn.execute('''CREATE TABLE video_job_origins (
        job_id TEXT PRIMARY KEY CHECK(length(job_id)=32),
        provider TEXT NOT NULL CHECK(provider='agnes_video'), model TEXT NOT NULL,
        intent_digest TEXT NOT NULL CHECK(length(intent_digest)=64),
        connection_digest TEXT NOT NULL CHECK(length(connection_digest)=64),
        external_task_digest TEXT CHECK(external_task_digest IS NULL OR length(external_task_digest)=64),
        created_at TEXT NOT NULL, submitted_at TEXT,
        CHECK((external_task_digest IS NULL AND submitted_at IS NULL)
              OR (external_task_digest IS NOT NULL AND submitted_at IS NOT NULL))
    )''')
    conn.execute('''CREATE TRIGGER video_job_origin_no_replace BEFORE INSERT ON video_job_origins
        WHEN EXISTS(SELECT 1 FROM video_job_origins WHERE job_id=NEW.job_id)
        BEGIN SELECT RAISE(ABORT,'video_job_origin_immutable'); END''')
    conn.execute('''CREATE TRIGGER video_job_origin_immutable BEFORE UPDATE ON video_job_origins
        WHEN NEW.job_id IS NOT OLD.job_id OR NEW.provider IS NOT OLD.provider OR NEW.model IS NOT OLD.model
          OR NEW.intent_digest IS NOT OLD.intent_digest OR NEW.connection_digest IS NOT OLD.connection_digest
          OR NEW.created_at IS NOT OLD.created_at OR OLD.external_task_digest IS NOT NULL
          OR NEW.external_task_digest IS NULL OR NEW.submitted_at IS NULL
        BEGIN SELECT RAISE(ABORT,'video_job_origin_immutable'); END''')
    conn.execute('''CREATE TRIGGER video_job_origin_retained BEFORE DELETE ON video_job_origins
        BEGIN SELECT RAISE(ABORT,'video_job_origin_retained'); END''')
