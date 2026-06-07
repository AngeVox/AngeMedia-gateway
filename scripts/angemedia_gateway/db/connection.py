"""SQLite DB connection / transaction 基础能力。"""
from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager

from .. import config as C


def db_connect() -> sqlite3.Connection:
    C.DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(C.DB_FILE, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_transaction(immediate: bool = False):
    """在 autocommit 连接上显式开启事务，统一多语句写入的提交/回滚语义。"""
    with closing(db_connect()) as conn:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
