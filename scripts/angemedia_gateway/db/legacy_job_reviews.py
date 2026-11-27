"""旧任务的审阅记录独立追加，保留原任务及其清理规则。"""
import sqlite3

LEGACY_JOB_REVIEWS_VERSION = 'legacy_job_reviews_v1'


def migrate_legacy_job_reviews(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE legacy_job_reviews (
            sequence INTEGER PRIMARY KEY,
            request_key TEXT NOT NULL UNIQUE CHECK(length(request_key)=32),
            job_id TEXT NOT NULL CHECK(length(job_id)=32),
            evidence_digest TEXT NOT NULL CHECK(length(evidence_digest)=64),
            evidence_json TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
    """)
    conn.execute('CREATE INDEX legacy_job_reviews_latest ON legacy_job_reviews(job_id, sequence DESC)')
    for operation in ('UPDATE', 'DELETE'):
        conn.execute(f"""
            CREATE TRIGGER legacy_job_reviews_{operation.lower()} BEFORE {operation} ON legacy_job_reviews
            BEGIN SELECT RAISE(ABORT, 'legacy_job_review_immutable'); END
        """)
    # REPLACE 可以绕开普通的删除触发器，追加表须同时保护两个唯一键。
    conn.execute("""
        CREATE TRIGGER legacy_job_reviews_no_replace BEFORE INSERT ON legacy_job_reviews
        WHEN EXISTS(SELECT 1 FROM legacy_job_reviews WHERE request_key=NEW.request_key OR sequence=NEW.sequence)
        BEGIN SELECT RAISE(ABORT, 'legacy_job_review_immutable'); END
    """)
