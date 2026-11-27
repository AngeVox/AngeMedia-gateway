"""最新连接观察的持久化与并发顺序；不保存明文配置快照。"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import Any

from ..db.connection import db_connect, db_transaction


def begin_connection_probe(provider_id: str, fingerprint_key: str, fingerprint: str, started_at: str, expires_at: str) -> int:
    """先声明新探测；旧探测即使后返回，也不能覆盖它。"""
    with db_transaction(immediate=True) as conn:
        conn.execute(
            "INSERT INTO provider_connection_evidence(provider_id,revision,fingerprint_key,fingerprint,started_at,expires_at) "
            "VALUES(?,1,?,?,?,?) ON CONFLICT(provider_id) DO UPDATE SET "
            "revision=revision+1,fingerprint_key=excluded.fingerprint_key,fingerprint=excluded.fingerprint,"
            "started_at=excluded.started_at,expires_at=excluded.expires_at,invalidated=0,result_json=NULL",
            (provider_id, fingerprint_key, fingerprint, started_at, expires_at),
        )
        return int(conn.execute("SELECT revision FROM provider_connection_evidence WHERE provider_id=?", (provider_id,)).fetchone()[0])


def finish_connection_probe(provider_id: str, revision: int, result: dict[str, Any], expires_at: str) -> bool:
    """仅当前探测可以落盘；网络期间不持有数据库锁。"""
    with db_transaction(immediate=True) as conn:
        changed = conn.execute(
            "UPDATE provider_connection_evidence SET result_json=?,expires_at=? "
            "WHERE provider_id=? AND revision=? AND invalidated=0",
            (json.dumps(result, ensure_ascii=False), expires_at, provider_id, revision),
        )
        return changed.rowcount == 1


def get_connection_evidence(provider_id: str) -> dict[str, Any] | None:
    """私有记录只供服务层校验；指纹材料不得直接返回API。"""
    with closing(db_connect()) as conn:
        row = conn.execute("SELECT * FROM provider_connection_evidence WHERE provider_id=?", (provider_id,)).fetchone()
    return dict(row) if row is not None else None


def invalidate_connection_evidence(conn: sqlite3.Connection, provider_id: str | None = None) -> None:
    """复用配置写入事务，令旧结果和在途探测同时失效。"""
    if provider_id is None:
        conn.execute("UPDATE provider_connection_evidence SET revision=revision+1,invalidated=1")
    elif provider_id == "agnes_image":
        # 视频可能继承图片渠道密钥，不能只使图片观察失效。
        conn.execute("UPDATE provider_connection_evidence SET revision=revision+1,invalidated=1 WHERE provider_id IN ('agnes_image','agnes_video')")
    else:
        conn.execute("UPDATE provider_connection_evidence SET revision=revision+1,invalidated=1 WHERE provider_id=?", (provider_id,))
