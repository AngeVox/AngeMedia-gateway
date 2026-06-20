"""Video task repository."""
from __future__ import annotations

from contextlib import closing
from typing import Any

from ..db.connection import db_connect
from ..helpers import now_iso, safe_json
from ..media import is_generated_local_url
from ..security import validate_task_id


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
) -> None:
    task_id = validate_task_id(task_id)
    video_url = str(result.get("video_url") or "")
    local_video_url = video_url if is_generated_local_url(video_url) else ""
    with closing(db_connect()) as conn:
        conn.execute(
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
                task_id, prompt, model, status,
                local_video_url,
                "",
                str(result.get("local_path") or ""),
                _safe_task_summary(result, task_id, status),
                now_iso(), now_iso(), "agnes_video", int(duration_ms or 0),
            ),
        )
