"""连接初始化时的WAL切换；锁竞争有界等待，不重放事务或业务写入。"""
from __future__ import annotations

import sqlite3
import time


def enable_wal(conn: sqlite3.Connection, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    # 切换日志模式可能直接返回BUSY，不保证调用SQLite忙处理器；统一使用总体时限。
    conn.execute('PRAGMA busy_timeout=0')
    try:
        while True:
            try:
                mode = conn.execute('PRAGMA journal_mode=WAL').fetchone()[0]
                if str(mode).lower() != 'wal':
                    raise sqlite3.OperationalError('SQLite WAL mode unavailable')
                return
            except sqlite3.OperationalError as exc:
                if (getattr(exc, 'sqlite_errorcode', 0) & 255) != sqlite3.SQLITE_BUSY:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(0.01, remaining))
    finally:
        conn.execute('PRAGMA busy_timeout=5000')
