"""Assets 相关 DB helper。"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

from fastapi import HTTPException

from .. import config as C
from ..db.connection import db_connect
from ..helpers import now_iso, safe_unlink_under
from ..job_sanitizer import sanitize_error_text


def save_asset(
    *,
    id: str,
    filename: str,
    storage_area: str,
    relative_path: str,
    url_path: str,
    media_type: str,
    source: str,
    size: int = 0,
    prompt: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    duration_ms: int | None = None,
    job_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """写入资产记录，(storage_area, relative_path) 冲突时更新 metadata，保留 created_at。

    job_id 冲突处理：
    - 新 job_id 为 None → 不覆盖已有 job_id
    - 已有 job_id 为 NULL 且新 job_id 非空 → 补写新 job_id
    - 已有 job_id 非空 → 不覆盖已有 job_id
    """
    now = now_iso()
    def write(connection: sqlite3.Connection) -> None:
        connection.execute(
                """
                INSERT INTO assets(
                    id, filename, storage_area, relative_path, url_path,
                    media_type, source, size, prompt, model, provider,
                    duration_ms, created_at, job_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(storage_area, relative_path) DO UPDATE SET
                    filename=excluded.filename,
                    url_path=excluded.url_path,
                    media_type=excluded.media_type,
                    source=excluded.source,
                    size=excluded.size,
                    prompt=excluded.prompt,
                    model=excluded.model,
                    provider=excluded.provider,
                    duration_ms=excluded.duration_ms,
                    job_id=CASE WHEN assets.job_id IS NULL AND excluded.job_id IS NOT NULL THEN excluded.job_id ELSE assets.job_id END
                """,
                (
                    id, filename, storage_area, relative_path, url_path,
                    media_type, source, size, sanitize_error_text(prompt, limit=8000),
                    sanitize_error_text(model, limit=256), sanitize_error_text(provider, limit=256),
                    duration_ms, now, job_id,
                ),
        )

    try:
        if conn is not None:
            write(conn)
        else:
            with closing(db_connect()) as connection:
                write(connection)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail="资产记录写入失败") from exc


def get_asset(asset_id: str) -> dict[str, Any] | None:
    """按 ID 查询单条资产，不存在时返回 None。"""
    with closing(db_connect()) as conn:
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def list_assets(limit: int = 100, offset: int = 0, job_id: str | None = None) -> list[dict[str, Any]]:
    """按 created_at DESC 分页列出资产，支持 job_id 过滤。"""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    if job_id is not None:
        sql = "SELECT * FROM assets WHERE job_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params: list[Any] = [job_id, limit, offset]
    else:
        sql = "SELECT * FROM assets ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params = [limit, offset]
    with closing(db_connect()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def delete_asset(asset_id: str) -> bool:
    """删除资产记录及其关联文件，返回 True 表示记录存在。

    顺序：查询 → 安全删除文件 → 删除 DB 记录。
    safe_unlink_under 抛异常时 DB 记录不被删除。
    """
    with closing(db_connect()) as conn:
        row = conn.execute(
            "SELECT storage_area, relative_path FROM assets WHERE id = ?",
            (asset_id,),
        ).fetchone()
    if row is None:
        return False
    storage_area = str(row["storage_area"])
    relative_path = str(row["relative_path"])
    base_dir = C.OUTPUT_DIR if storage_area == "output" else C.UPLOAD_DIR
    # 先安全删除文件；抛 HTTPException 时不删除 DB 记录
    safe_unlink_under(str(base_dir / relative_path), base_dir)
    # 文件删除成功或文件不存在，删除 DB 记录
    with closing(db_connect()) as conn:
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    return True
