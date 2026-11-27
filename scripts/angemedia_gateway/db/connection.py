"""SQLite连接与事务基础能力。"""
from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from .. import config as C
from .readonly_scope import readonly_target
from .journal_mode import enable_wal


@contextmanager
def readonly_snapshot(path):
    """显式文件的单一只读快照；不建目录、不启WAL、不忽略尚未归并的WAL。"""
    target = Path(path).resolve(strict=True)
    if not target.is_file():
        raise ValueError('inventory_database_unavailable')
    with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True, isolation_level=None)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA busy_timeout=5000')
        conn.execute('BEGIN')
        try:
            yield conn
        finally:
            conn.execute('ROLLBACK')


def db_connect(*, path: Path | None = None) -> sqlite3.Connection:
    scoped = readonly_target(path)
    if scoped is not None:
        conn = sqlite3.connect(scoped.as_uri() + "?mode=ro", uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA query_only=ON")
        return conn
    target = C.DB_FILE if path is None else Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        enable_wal(conn)
        conn.execute("PRAGMA foreign_keys=ON")
    except BaseException:
        # 初始化失败的连接不交给调用方，也不能泄漏文件锁。
        conn.close()
        raise
    return conn


@contextmanager
def db_transaction(immediate: bool = False, *, path: Path | None = None):
    """在 autocommit 连接上显式开启事务，统一多语句写入的提交/回滚语义。"""
    with closing(db_connect(path=path)) as conn:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
