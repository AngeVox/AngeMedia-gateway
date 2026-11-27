"""入队可加入上层授权事务，同时保证局部失败不会留下半个任务。"""
from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

from ..db.connection import db_transaction


@contextmanager
def admission_transaction(conn=None):
    if conn is None:
        with db_transaction(immediate=True) as owned:
            yield owned
        return
    if not conn.in_transaction:
        raise ValueError('加入入队操作须提供已开启的写事务')
    name = 'admission_' + uuid4().hex
    conn.execute(f'SAVEPOINT {name}')
    try:
        yield conn
        conn.execute(f'RELEASE SAVEPOINT {name}')
    except BaseException:
        conn.execute(f'ROLLBACK TO SAVEPOINT {name}')
        conn.execute(f'RELEASE SAVEPOINT {name}')
        raise
