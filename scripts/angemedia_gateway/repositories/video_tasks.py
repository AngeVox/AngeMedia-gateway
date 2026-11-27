"""Video task repository."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import Any

from ..db.connection import db_connect, db_transaction
from ..helpers import now_iso, safe_json
from ..job_sanitizer import sanitize_error_text
from ..media import is_generated_local_url
from ..security import validate_provider_external_id


def get_video_task(task_id: str) -> dict[str, Any] | None:
    """只读本地旧记录，不将其当作当前账户的远端查询授权。"""
    with closing(db_connect()) as conn:
        row = conn.execute('SELECT * FROM video_tasks WHERE task_id=?', (task_id,)).fetchone()
    return dict(row) if row else None


def _safe_task_summary(result: dict[str, Any], task_id: str, status: str) -> str:
    """Persist lifecycle facts only; never retain the provider's raw response."""
    return safe_json({
        "task_id": task_id,
        "status": status,
        "provider": "agnes_video",
        "model": str(result.get("model") or "")[:160],
        "localized": result.get("localized") is True,
        "has_video_url": bool(result.get("video_url")),
        "has_local_path": bool(result.get("local_path")),
        "duration_ms": int(result.get("duration_ms") or 0),
    })


def upsert_video_task(
    task_id: str,
    prompt: str,
    model: str,
    status: str,
    result: dict[str, Any],
    duration_ms: int = 0,
    conn: sqlite3.Connection | None = None,
) -> None:
    task_id = validate_provider_external_id(task_id)
    video_url = str(result.get("video_url") or "")
    local_video_url = video_url if is_generated_local_url(video_url) else ""
    def write(connection: sqlite3.Connection) -> None:
        previous = connection.execute('SELECT * FROM video_tasks WHERE task_id=?', (task_id,)).fetchone()
        local_url = local_video_url or (previous['video_url'] if previous else '')
        local_path = str(result.get('local_path') or '') or (previous['local_path'] if previous else '')
        elapsed = int(duration_ms or 0) or (int(previous['duration_ms'] or 0) if previous else 0)
        try:
            old_summary = json.loads(previous['raw_json'] or '{}') if previous else {}
        except (TypeError, ValueError):
            old_summary = {}
        # 缺省的状态观察不能擦掉已保存作品；保留的本地事实不改变新的上游状态。
        summary = {**result, 'model': result.get('model') or (previous['model'] if previous else model),
                   'local_path': local_path, 'video_url': local_url or video_url,
                   'localized': result.get('localized') is True or (isinstance(old_summary, dict) and old_summary.get('localized') is True),
                   'duration_ms': elapsed}
        connection.execute(
            """
            INSERT INTO video_tasks(task_id,prompt,model,status,video_url,remote_video_url,local_path,raw_json,created_at,updated_at,provider,duration_ms)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(task_id) DO UPDATE SET
                status=excluded.status,
                video_url=excluded.video_url,
                remote_video_url=excluded.remote_video_url,
                local_path=excluded.local_path,
                raw_json=excluded.raw_json,
                provider=excluded.provider,
                duration_ms=excluded.duration_ms,
                updated_at=excluded.updated_at
            """,
            (
                task_id, sanitize_error_text(prompt, limit=8000) or "",
                sanitize_error_text(model, limit=256) or "", sanitize_error_text(status, limit=64) or "unknown",
                local_url,
                "",
                local_path,
                _safe_task_summary(summary, task_id, status),
                now_iso(), now_iso(), "agnes_video", elapsed,
            ),
        )

    if conn is not None:
        write(conn)
    else:
        with db_transaction(immediate=True) as connection:
            write(connection)
