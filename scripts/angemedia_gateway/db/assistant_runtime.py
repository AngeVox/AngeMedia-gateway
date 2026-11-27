"""旧助手运行表升级及运行控制表，保持事务由迁移器统一管理。"""
from __future__ import annotations

import sqlite3


def ensure_assistant_base_schema(conn: sqlite3.Connection) -> None:
    """基础表只定义一次，同时支持完整启动和旧库独立迁移入口。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assistant_sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assistant_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            safe_payload_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES assistant_sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assistant_runs (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('succeeded', 'failed', 'refused')),
            skill_id TEXT,
            input_json TEXT NOT NULL,
            output_json TEXT NOT NULL,
            timeline_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES assistant_sessions(id) ON DELETE CASCADE
        )
    """)


def migrate_assistant_runtime(conn: sqlite3.Connection) -> None:
    ensure_assistant_base_schema(conn)
    if any(row[1] == "runtime_managed" for row in conn.execute("PRAGMA table_info(assistant_runs)")):
        raise RuntimeError("助手运行表已扩展但迁移标记缺失，停止重建以保留运行数据")
    # 旧表只允许终态且completed_at不可空，需要重建；旧九列逐字保留。
    conn.execute("""
        CREATE TABLE assistant_runs_runtime_v1 (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','running','paused','succeeded','failed','refused','canceled','timed_out','budget_exceeded')),
            skill_id TEXT, input_json TEXT NOT NULL, output_json TEXT NOT NULL,
            timeline_json TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT,
            runtime_managed INTEGER NOT NULL DEFAULT 0 CHECK(runtime_managed IN (0,1)),
            request_key TEXT, updated_at TEXT NOT NULL DEFAULT '', started_at TEXT,
            cancel_requested_at TEXT, version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0),
            event_seq INTEGER NOT NULL DEFAULT 0 CHECK(event_seq>=0),
            timeout_ms INTEGER, deadline_ms INTEGER,
            max_model_calls INTEGER NOT NULL DEFAULT 8 CHECK(max_model_calls>0),
            model_calls INTEGER NOT NULL DEFAULT 0 CHECK(model_calls>=0 AND model_calls<=max_model_calls),
            max_tool_calls INTEGER NOT NULL DEFAULT 16 CHECK(max_tool_calls>0),
            tool_calls INTEGER NOT NULL DEFAULT 0 CHECK(tool_calls>=0 AND tool_calls<=max_tool_calls),
            CHECK(runtime_managed=0 OR (request_key IS NOT NULL AND timeout_ms IS NOT NULL AND timeout_ms>0 AND deadline_ms IS NOT NULL)),
            CHECK(runtime_managed=0 OR
                (status IN ('queued','running','paused') AND completed_at IS NULL) OR
                (status IN ('succeeded','failed','refused','canceled','timed_out','budget_exceeded') AND completed_at IS NOT NULL)),
            FOREIGN KEY(session_id) REFERENCES assistant_sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        INSERT INTO assistant_runs_runtime_v1
            (id,session_id,status,skill_id,input_json,output_json,timeline_json,created_at,completed_at,updated_at)
        SELECT id,session_id,status,skill_id,input_json,output_json,timeline_json,created_at,completed_at,created_at
        FROM assistant_runs
    """)
    conn.execute("DROP TABLE assistant_runs")
    conn.execute("ALTER TABLE assistant_runs_runtime_v1 RENAME TO assistant_runs")
    for statement in (
        "CREATE UNIQUE INDEX uq_assistant_run_request ON assistant_runs(session_id,request_key) WHERE runtime_managed=1",
        "CREATE INDEX idx_assistant_runtime_queue ON assistant_runs(session_id,runtime_managed,status)",
        "CREATE INDEX idx_assistant_runtime_deadline ON assistant_runs(deadline_ms) WHERE runtime_managed=1 AND status IN ('queued','running','paused')",
        """CREATE TABLE assistant_session_leases (
            session_id TEXT PRIMARY KEY REFERENCES assistant_sessions(id) ON DELETE CASCADE,
            fence INTEGER NOT NULL DEFAULT 0 CHECK(fence>=0),
            run_id TEXT UNIQUE REFERENCES assistant_runs(id) ON DELETE RESTRICT,
            owner_token TEXT, expires_ms INTEGER,
            CHECK((run_id IS NULL AND owner_token IS NULL AND expires_ms IS NULL)
                OR (run_id IS NOT NULL AND owner_token IS NOT NULL AND expires_ms IS NOT NULL))
        )""",
        """CREATE TABLE assistant_run_events (
            run_id TEXT NOT NULL REFERENCES assistant_runs(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL CHECK(sequence>0), event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(run_id,sequence)
        )""",
    ):
        conn.execute(statement)
