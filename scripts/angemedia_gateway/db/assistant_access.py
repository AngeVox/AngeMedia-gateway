"""账户会话的权限迁移；旧会话维持共享，禁止隐式认领或降回共享。"""
from __future__ import annotations

import sqlite3


def migrate_assistant_access(conn: sqlite3.Connection) -> None:
    if any(row[1] == "access_scope" for row in conn.execute("PRAGMA table_info(assistant_sessions)")):
        raise RuntimeError("助手权限列已存在但迁移标记缺失，停止迁移以保留归属")
    conn.execute("ALTER TABLE assistant_sessions ADD COLUMN access_scope TEXT NOT NULL "
                 "DEFAULT 'shared' CHECK(access_scope IN ('shared','account'))")
    conn.execute("""
        CREATE TABLE assistant_session_owners (
            session_id TEXT PRIMARY KEY REFERENCES assistant_sessions(id) ON DELETE CASCADE,
            username TEXT NOT NULL REFERENCES admin_users(username) ON UPDATE CASCADE ON DELETE RESTRICT
        )
    """)
    conn.execute("CREATE INDEX idx_assistant_session_owner ON assistant_session_owners(username,session_id)")
    conn.execute("""
        CREATE TRIGGER assistant_session_scope_immutable BEFORE UPDATE OF access_scope ON assistant_sessions
        WHEN NEW.access_scope != OLD.access_scope
        BEGIN SELECT RAISE(ABORT, 'assistant session scope is immutable'); END
    """)
    conn.execute("""
        CREATE TRIGGER assistant_session_owner_scope BEFORE INSERT ON assistant_session_owners
        WHEN NOT EXISTS(SELECT 1 FROM assistant_sessions WHERE id=NEW.session_id AND access_scope='account')
        BEGIN SELECT RAISE(ABORT, 'assistant ownership requires account scope'); END
    """)
