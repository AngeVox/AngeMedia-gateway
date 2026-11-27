"""动作提案/修订与确认收据；归档擦除正文，但保留已消费动作的归属和任务引用。"""
from __future__ import annotations

import sqlite3


def migrate_assistant_actions(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE assistant_actions (
            id TEXT PRIMARY KEY CHECK(length(id)=32),
            session_id TEXT REFERENCES assistant_sessions(id) ON DELETE SET NULL,
            origin_session_id TEXT NOT NULL,
            username TEXT NOT NULL REFERENCES admin_users(username) ON UPDATE CASCADE ON DELETE RESTRICT,
            creation_key TEXT NOT NULL,
            creation_fingerprint TEXT NOT NULL,
            version INTEGER NOT NULL CHECK(version>0),
            status TEXT NOT NULL CHECK(status IN ('draft','revoked','submitted')),
            job_id TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            confirmed_at TEXT, confirmed_acks_json TEXT,
            CHECK((status='submitted' AND job_id IS NOT NULL AND confirmed_at IS NOT NULL AND confirmed_acks_json IS NOT NULL)
                  OR (status!='submitted' AND job_id IS NULL AND confirmed_at IS NULL AND confirmed_acks_json IS NULL)),
            UNIQUE(origin_session_id,creation_key)
        )
    """)
    conn.execute("""
        CREATE TABLE assistant_action_revisions (
            action_id TEXT NOT NULL REFERENCES assistant_actions(id) ON DELETE CASCADE,
            version INTEGER NOT NULL CHECK(version>0), request_key TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL, binding_digest TEXT NOT NULL,
            body_json TEXT, expires_ms INTEGER NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(action_id,version), UNIQUE(action_id,request_key)
        )
    """)
    conn.execute('CREATE INDEX idx_assistant_actions_owner ON assistant_actions(username,status,updated_at)')
    conn.execute('CREATE UNIQUE INDEX uq_assistant_action_job ON assistant_actions(job_id) WHERE job_id IS NOT NULL')
    conn.execute('CREATE INDEX idx_assistant_actions_session ON assistant_actions(session_id,status)')
    # 修订只能追加，正文仅允许为保留清理置空；摘要、期限和请求标识不允许事后替换。
    conn.execute("""
        CREATE TRIGGER assistant_action_revision_immutable BEFORE UPDATE ON assistant_action_revisions
        WHEN NEW.action_id IS NOT OLD.action_id OR NEW.version IS NOT OLD.version
          OR NEW.request_key IS NOT OLD.request_key OR NEW.request_fingerprint IS NOT OLD.request_fingerprint
          OR NEW.binding_digest IS NOT OLD.binding_digest OR NEW.expires_ms IS NOT OLD.expires_ms
          OR NEW.created_at IS NOT OLD.created_at
          OR (NEW.body_json IS NOT OLD.body_json AND NEW.body_json IS NOT NULL)
        BEGIN SELECT RAISE(ABORT,'action_revision_immutable'); END
    """)
    conn.execute("""
        CREATE TRIGGER assistant_action_identity_immutable BEFORE UPDATE ON assistant_actions
        WHEN NEW.id IS NOT OLD.id OR NEW.origin_session_id IS NOT OLD.origin_session_id
          OR NEW.creation_key IS NOT OLD.creation_key OR NEW.creation_fingerprint IS NOT OLD.creation_fingerprint
          OR NEW.created_at IS NOT OLD.created_at
          OR (OLD.status='submitted' AND (NEW.status IS NOT OLD.status OR NEW.job_id IS NOT OLD.job_id
              OR NEW.version IS NOT OLD.version OR NEW.confirmed_at IS NOT OLD.confirmed_at
              OR NEW.confirmed_acks_json IS NOT OLD.confirmed_acks_json))
          OR (OLD.status='revoked' AND NEW.status!='revoked')
        BEGIN SELECT RAISE(ABORT,'action_identity_immutable'); END
    """)
    # 直接SQL归档、旧仓库入口及保留清理共用同一不可跳过的生命周期约束。
    for event, condition in [('UPDATE OF status', "WHEN NEW.status='archived'"), ('DELETE', '')]:
        name = 'archive' if event.startswith('UPDATE') else 'delete'
        conn.execute(f"""
            CREATE TRIGGER assistant_actions_session_{name} BEFORE {event} ON assistant_sessions {condition}
            BEGIN
                UPDATE assistant_action_revisions SET body_json=NULL
                    WHERE action_id IN (SELECT id FROM assistant_actions WHERE session_id=OLD.id);
                UPDATE assistant_actions SET status=CASE WHEN status='draft' THEN 'revoked' ELSE status END
                    WHERE session_id=OLD.id;
            END
        """)
