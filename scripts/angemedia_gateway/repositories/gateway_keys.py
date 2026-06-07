"""API 模式 API Key 相关 DB helper。"""
from __future__ import annotations

import hmac
import uuid
from contextlib import closing
from typing import Any

from ..db.connection import db_connect
from ..helpers import now_iso
from ..security import generate_gateway_key, hash_token


def create_gateway_api_key(*, name: str = "", note: str | None = None) -> dict[str, Any]:
    """创建新 API Key。返回完整 key（仅此一次可见）。"""
    full_key = generate_gateway_key()
    key_id = uuid.uuid4().hex
    key_prefix = full_key[:11]
    key_hash = hash_token(full_key)
    now = now_iso()
    with closing(db_connect()) as conn:
        conn.execute(
            "INSERT INTO gateway_api_keys(id,name,key_prefix,key_hash,enabled,note,created_at) "
            "VALUES(?,?,?,?,1,?,?)",
            (key_id, name, key_prefix, key_hash, note, now),
        )
    return {
        "id": key_id,
        "name": name,
        "key": full_key,
        "key_prefix": key_prefix,
        "enabled": True,
        "note": note,
        "created_at": now,
        "last_used_at": None,
        "last_used_ip": None,
        "revoked_at": None,
    }


def list_gateway_api_keys() -> list[dict[str, Any]]:
    """列出所有 API Key（不返回 key_hash）。"""
    with closing(db_connect()) as conn:
        rows = conn.execute(
            "SELECT id,name,key_prefix,enabled,note,created_at,last_used_at,last_used_ip,revoked_at "
            "FROM gateway_api_keys ORDER BY created_at DESC"
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        result.append(item)
    return result


def get_gateway_api_key(key_id: str) -> dict[str, Any] | None:
    """按 ID 查询单条 API Key（不返回 key_hash）。"""
    with closing(db_connect()) as conn:
        row = conn.execute(
            "SELECT id,name,key_prefix,enabled,note,created_at,last_used_at,last_used_ip,revoked_at "
            "FROM gateway_api_keys WHERE id = ?",
            (key_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    return item


def update_gateway_api_key(
    key_id: str,
    *,
    name: str | None = None,
    note: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any] | None:
    """更新 API Key 的 name / note / enabled 字段。"""
    with closing(db_connect()) as conn:
        existing = conn.execute(
            "SELECT id,name,note,enabled FROM gateway_api_keys WHERE id = ?",
            (key_id,),
        ).fetchone()
    if existing is None:
        return None
    new_name = name if name is not None else str(existing["name"])
    new_note = note if note is not None else (str(existing["note"]) if existing["note"] is not None else None)
    new_enabled = 1 if enabled else 0 if enabled is not None else int(existing["enabled"])
    with closing(db_connect()) as conn:
        conn.execute(
            "UPDATE gateway_api_keys SET name=?, note=?, enabled=? WHERE id=?",
            (new_name, new_note, new_enabled, key_id),
        )
    return get_gateway_api_key(key_id)


def revoke_gateway_api_key(key_id: str) -> bool:
    """吊销 API Key（设置 revoked_at，不删除记录）。"""
    now = now_iso()
    with closing(db_connect()) as conn:
        cursor = conn.execute(
            "UPDATE gateway_api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
            (now, key_id),
        )
    return cursor.rowcount > 0


def has_gateway_api_key_records() -> bool:
    """判断是否曾创建过 Gateway API Key 记录。"""
    with closing(db_connect()) as conn:
        row = conn.execute("SELECT 1 FROM gateway_api_keys LIMIT 1").fetchone()
    return row is not None


def verify_gateway_api_key(input_key: str) -> dict[str, Any] | None:
    """验证 API Key：enabled=1 且未吊销。返回 key 记录（不含 key_hash）。"""
    if not input_key:
        return None
    digest = hash_token(input_key)
    with closing(db_connect()) as conn:
        row = conn.execute(
            "SELECT id,name,key_prefix,key_hash,enabled,note,created_at,last_used_at,last_used_ip,revoked_at "
            "FROM gateway_api_keys WHERE key_hash=? AND enabled=1 AND revoked_at IS NULL",
            (digest,),
        ).fetchone()
    if row is None:
        return None
    # Timing-safe hash comparison
    if not hmac.compare_digest(digest, str(row["key_hash"])):
        return None
    item = dict(row)
    item.pop("key_hash", None)
    item["enabled"] = bool(item["enabled"])
    return item


def update_gateway_api_key_last_used(key_id: str, ip: str | None = None) -> bool:
    """更新 API Key 的 last_used_at 和 last_used_ip。"""
    now = now_iso()
    with closing(db_connect()) as conn:
        cursor = conn.execute(
            "UPDATE gateway_api_keys SET last_used_at=?, last_used_ip=? WHERE id=?",
            (now, ip, key_id),
        )
    return cursor.rowcount > 0
