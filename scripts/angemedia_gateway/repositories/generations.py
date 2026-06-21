"""Generated media history repository."""
from __future__ import annotations

import uuid
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..db.connection import db_connect, db_transaction
from ..helpers import first_result_url, now_iso
from ..job_sanitizer import sanitize_error_text, sanitized_json


def record_generation(
    media_type: str,
    prompt: str,
    enhanced_prompt: str | None,
    model: str | None,
    status: str,
    result: dict[str, Any],
    task_id: str | None = None,
    provider: str | None = None,
    request_model: str | None = None,
    input_mode: str | None = None,
    duration_ms: int = 0,
    started_at: str | None = None,
    job_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> str:
    record_id = uuid.uuid4().hex
    result_url, remote_url, local_path = first_result_url(result)
    if job_id:
        parsed = urlparse(result_url)
        result_url = (
            parsed.path
            if not parsed.query and not parsed.fragment and parsed.path.startswith("/generated/")
            else ""
        )
        remote_url = ""
        prompt = sanitize_error_text(prompt, limit=8000) or ""
    completed_at = now_iso()
    started_at = started_at or completed_at
    def write(connection: sqlite3.Connection) -> str:
        if job_id:
            existing = connection.execute(
                "SELECT id FROM generations WHERE job_id=?", (job_id,)
            ).fetchone()
            if existing is not None:
                return str(existing["id"])
        stored_result = _queued_result_summary(result) if job_id else result
        try:
            connection.execute(
                """
                INSERT INTO generations(
                    id, media_type, prompt, enhanced_prompt, model, status,
                    result_url, remote_url, local_path, task_id, job_id, raw_json, created_at, updated_at,
                    provider, request_model, input_mode, duration_ms, started_at, completed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record_id, media_type, prompt, enhanced_prompt, model, status,
                    result_url, remote_url, local_path, task_id, job_id,
                    sanitized_json(stored_result), completed_at, completed_at,
                    provider, request_model, input_mode, int(duration_ms or 0), started_at, completed_at,
                ),
            )
        except sqlite3.IntegrityError:
            if not job_id:
                raise
            existing = connection.execute(
                "SELECT id FROM generations WHERE job_id=?", (job_id,)
            ).fetchone()
            if existing is None:
                raise
            return str(existing["id"])
        return record_id

    if conn is not None:
        return write(conn)
    with closing(db_connect()) as connection:
        return write(connection)


def _queued_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Persist only controlled asset identities for worker-owned generations."""
    items: list[dict[str, str]] = []
    data = result.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            value = str(item.get("url") or "")
            parsed = urlparse(value)
            if not parsed.query and not parsed.fragment and parsed.path.startswith("/generated/"):
                items.append({"url": parsed.path})
    return {
        "data": items,
        "provider": result.get("provider", ""),
        "model": result.get("model", ""),
        "duration_ms": result.get("duration_ms", 0),
    }


def list_generation_files() -> list[dict[str, Any]]:
    with closing(db_connect()) as conn:
        rows = conn.execute("SELECT result_url, remote_url, local_path FROM generations").fetchall()
    return [dict(row) for row in rows]


def generation_metadata_by_filename() -> dict[str, dict[str, Any]]:
    """Index latest generation metadata by local filename."""

    with closing(db_connect()) as conn:
        rows = conn.execute(
            """
            SELECT media_type, prompt, model, provider, request_model, input_mode, duration_ms,
                   status, result_url, local_path, task_id, created_at, updated_at
            FROM generations
            WHERE local_path IS NOT NULL AND local_path != ''
            ORDER BY created_at DESC
            """
        ).fetchall()
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        path = str(row["local_path"] or "")
        filename = Path(path).name if path else ""
        if not filename or filename in indexed:
            continue
        item = dict(row)
        item["filename"] = filename
        indexed[filename] = item
    return indexed


def clear_generations() -> None:
    with closing(db_connect()) as conn:
        conn.execute("DELETE FROM generations")


def delete_generation_records_for_file(local_path: str, result_url: str = "") -> int:
    with closing(db_connect()) as conn:
        cur = conn.execute(
            "DELETE FROM generations WHERE local_path = ? OR result_url = ?",
            (local_path, result_url),
        )
    return int(cur.rowcount or 0)


def clear_generations_and_collect_files() -> list[str]:
    """Collect local paths and clear generation history in one transaction."""

    with db_transaction(immediate=True) as conn:
        rows = conn.execute("SELECT local_path FROM generations WHERE local_path IS NOT NULL AND local_path != ''").fetchall()
        paths = [str(row["local_path"]) for row in rows]
        conn.execute("DELETE FROM generations")
        return paths


def known_generated_local_paths() -> set[str]:
    with closing(db_connect()) as conn:
        rows = conn.execute("SELECT local_path FROM generations WHERE local_path IS NOT NULL AND local_path != ''").fetchall()
    return {str(row["local_path"]) for row in rows}
