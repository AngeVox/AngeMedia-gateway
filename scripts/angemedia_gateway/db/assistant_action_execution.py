"""执行凭据与数据库专用绑定密钥；不把旧提案自动提升为执行授权。"""
from __future__ import annotations

import secrets
import sqlite3


def migrate_assistant_action_execution(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE assistant_action_binding_key (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL CHECK(length(value)=64))")
    conn.execute('INSERT INTO assistant_action_binding_key VALUES(1,?)', (secrets.token_hex(32),))
    for event in ('UPDATE', 'DELETE'):
        conn.execute(f"CREATE TRIGGER assistant_action_key_{event.lower()} BEFORE {event} ON assistant_action_binding_key "
                     "BEGIN SELECT RAISE(ABORT,'action_binding_key_immutable'); END")
    conn.execute("""
        CREATE TABLE assistant_action_execution_grants (
            job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
            action_id TEXT NOT NULL REFERENCES assistant_actions(id) ON DELETE RESTRICT,
            version INTEGER NOT NULL, binding_digest TEXT NOT NULL,
            intent_digest TEXT NOT NULL, connections_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(action_id,version) REFERENCES assistant_action_revisions(action_id,version)
        )
    """)
    conn.execute("""
        CREATE TRIGGER assistant_action_grant_insert BEFORE INSERT ON assistant_action_execution_grants
        WHEN NOT EXISTS(SELECT 1 FROM assistant_actions a JOIN assistant_action_revisions r
                        ON r.action_id=a.id AND r.version=a.version
                        WHERE a.id=NEW.action_id AND a.status='submitted' AND a.job_id=NEW.job_id
                          AND a.version=NEW.version AND r.binding_digest=NEW.binding_digest)
        BEGIN SELECT RAISE(ABORT,'action_grant_receipt_mismatch'); END
    """)
    conn.execute("CREATE TRIGGER assistant_action_grant_immutable BEFORE UPDATE ON assistant_action_execution_grants "
                 "BEGIN SELECT RAISE(ABORT,'action_grant_immutable'); END")
