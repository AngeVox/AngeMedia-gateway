"""SQLite 本地状态库。"""
from __future__ import annotations

import os
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from fastapi import HTTPException

from . import config as C
from .db.connection import db_connect, db_transaction
from .helpers import (
    PROVIDER_ID_RE,
    first_result_url,
    is_relative_to_path,
    now_iso,
    safe_json,
    safe_unlink_under,
    validate_provider_id,
)
from .repositories.settings import (
    BUILTIN_PROVIDER_CONFIG_KEYS,
    apply_saved_config_to_runtime,
    builtin_provider_enabled,
    config_snapshot,
    delete_custom_provider,
    get_config,
    get_custom_provider,
    list_custom_providers,
    load_saved_settings,
    mask_secret,
    set_builtin_provider_enabled,
    set_config,
    set_config_many,
    update_custom_provider_enabled,
    update_custom_provider_sort,
    update_custom_provider_test,
    upsert_custom_provider,
)
from .repositories.gateway_keys import (
    create_gateway_api_key,
    get_gateway_api_key,
    has_gateway_api_key_records,
    list_gateway_api_keys,
    revoke_gateway_api_key,
    update_gateway_api_key,
    update_gateway_api_key_last_used,
    verify_gateway_api_key,
)
from .security import generate_session_token, hash_password, hash_token, validate_task_id, verify_password


def init_db() -> None:
    with closing(db_connect()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS generations (
                id TEXT PRIMARY KEY,
                media_type TEXT NOT NULL,
                prompt TEXT NOT NULL,
                enhanced_prompt TEXT,
                model TEXT,
                status TEXT NOT NULL,
                result_url TEXT,
                remote_url TEXT,
                local_path TEXT,
                task_id TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS video_tasks (
                task_id TEXT PRIMARY KEY,
                prompt TEXT,
                model TEXT,
                status TEXT NOT NULL,
                video_url TEXT,
                remote_video_url TEXT,
                local_path TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                original_filename TEXT,
                role TEXT,
                content_type TEXT,
                url TEXT NOT NULL,
                local_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assistant_plans (
                id TEXT PRIMARY KEY,
                original_prompt TEXT NOT NULL,
                media_type TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS custom_providers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider_type TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT,
                default_model TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                status_url TEXT,
                quota_url TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_sessions (
                session_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires_at REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_login_attempts (
                attempt_key TEXT PRIMARY KEY,
                fail_count INTEGER NOT NULL,
                locked_until REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                storage_area TEXT NOT NULL CHECK(storage_area IN ('output', 'upload')),
                relative_path TEXT NOT NULL,
                url_path TEXT NOT NULL,
                media_type TEXT NOT NULL CHECK(media_type IN ('image', 'video')),
                source TEXT NOT NULL CHECK(source IN ('generated', 'upload')),
                size INTEGER NOT NULL DEFAULT 0,
                prompt TEXT,
                model TEXT,
                provider TEXT,
                duration_ms INTEGER,
                created_at TEXT NOT NULL,
                job_id TEXT,
                UNIQUE(storage_area, relative_path)
            );
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gateway_api_keys (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                key_prefix TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                note TEXT,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                last_used_ip TEXT,
                revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('image', 'video')),
                status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')),
                provider TEXT,
                model TEXT,
                prompt TEXT,
                input_json TEXT,
                output_json TEXT,
                error_code TEXT,
                error_message TEXT,
                external_task_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                duration_ms INTEGER
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
            ("baseline", now_iso()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
            ("gateway_api_keys_v1", now_iso()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
            ("jobs_v1", now_iso()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
            ("assets_job_id_v1", now_iso()),
        )
        ensure_columns(conn)


def ensure_columns(conn: sqlite3.Connection) -> None:
    """为旧版本 SQLite 状态库补齐新增列。"""
    additions = {
        "generations": {
            "provider": "TEXT",
            "request_model": "TEXT",
            "input_mode": "TEXT",
            "duration_ms": "INTEGER NOT NULL DEFAULT 0",
            "started_at": "TEXT",
            "completed_at": "TEXT",
        },
        "video_tasks": {
            "provider": "TEXT",
            "duration_ms": "INTEGER NOT NULL DEFAULT 0",
        },
        "custom_providers": {
            "sort_order": "INTEGER NOT NULL DEFAULT 100",
            "last_test_at": "TEXT",
            "last_test_status": "TEXT",
            "last_response_ms": "INTEGER NOT NULL DEFAULT 0",
            "last_error": "TEXT",
        },
        "assets": {
            "job_id": "TEXT",
        },
    }
    for table, columns in additions.items():
        existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


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
) -> str:
    record_id = uuid.uuid4().hex
    result_url, remote_url, local_path = first_result_url(result)
    completed_at = now_iso()
    started_at = started_at or completed_at
    with closing(db_connect()) as conn:
        conn.execute(
            """
            INSERT INTO generations(
                id, media_type, prompt, enhanced_prompt, model, status,
                result_url, remote_url, local_path, task_id, raw_json, created_at, updated_at,
                provider, request_model, input_mode, duration_ms, started_at, completed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record_id, media_type, prompt, enhanced_prompt, model, status,
                result_url, remote_url, local_path, task_id, safe_json(result), completed_at, completed_at,
                provider, request_model, input_mode, int(duration_ms or 0), started_at, completed_at,
            ),
        )
    return record_id


def upsert_video_task(
    task_id: str,
    prompt: str,
    model: str,
    status: str,
    result: dict[str, Any],
    duration_ms: int = 0,
) -> None:
    task_id = validate_task_id(task_id)
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
                str(result.get("video_url") or ""),
                str(result.get("remote_video_url") or ""),
                str(result.get("local_path") or ""),
                safe_json(result), now_iso(), now_iso(), "agnes_video", int(duration_ms or 0),
            ),
        )


TABLE_SELECT_SQL = {
    "generations": "SELECT * FROM generations ORDER BY created_at DESC LIMIT ?",
    "video_tasks": "SELECT * FROM video_tasks ORDER BY created_at DESC LIMIT ?",
    "uploads": "SELECT * FROM uploads ORDER BY created_at DESC LIMIT ?",
    "assistant_plans": "SELECT * FROM assistant_plans ORDER BY created_at DESC LIMIT ?",
}


def list_rows(table: str, limit: int = 50) -> list[dict[str, Any]]:
    sql = TABLE_SELECT_SQL.get(table)
    if sql is None:
        raise HTTPException(status_code=400, detail="不支持的表")
    limit = max(1, min(limit, 200))
    with closing(db_connect()) as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    return [dict(row) for row in rows]


def save_upload(row: dict[str, Any]) -> None:
    with closing(db_connect()) as conn:
        conn.execute(
            "INSERT INTO uploads(id,filename,original_filename,role,content_type,url,local_path,created_at) "
            "VALUES(:id,:filename,:original_filename,:role,:content_type,:url,:local_path,:created_at)",
            row,
        )


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
) -> None:
    """写入资产记录，(storage_area, relative_path) 冲突时更新 metadata，保留 created_at。

    job_id 冲突处理：
    - 新 job_id 为 None → 不覆盖已有 job_id
    - 已有 job_id 为 NULL 且新 job_id 非空 → 补写新 job_id
    - 已有 job_id 非空 → 不覆盖已有 job_id
    """
    now = now_iso()
    try:
        with closing(db_connect()) as conn:
            conn.execute(
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
                    media_type, source, size, prompt, model, provider,
                    duration_ms, now, job_id,
                ),
            )
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


def save_assistant_plan(plan_id: str, original_prompt: str, media_type: str, plan: dict[str, Any]) -> None:
    with closing(db_connect()) as conn:
        conn.execute(
            "INSERT INTO assistant_plans(id,original_prompt,media_type,plan_json,created_at) VALUES(?,?,?,?,?)",
            (plan_id, original_prompt, media_type, safe_json(plan), now_iso()),
        )


class DeleteUploadResult(NamedTuple):
    found: bool
    file_deleted: bool
    local_path: str = ""


def delete_upload(upload_id: str) -> DeleteUploadResult:
    with db_transaction(immediate=True) as conn:
        row = conn.execute("SELECT local_path FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        if row is None:
            return DeleteUploadResult(found=False, file_deleted=False)
        conn.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
    path = str(row["local_path"] or "")
    if not path:
        return DeleteUploadResult(found=True, file_deleted=True, local_path="")
    resolved = Path(path).expanduser().resolve()
    existed = resolved.exists() and resolved.is_file()
    deleted = safe_unlink_under(path, C.UPLOAD_DIR)
    if existed and not deleted:
        return DeleteUploadResult(found=True, file_deleted=False, local_path=path)
    return DeleteUploadResult(found=True, file_deleted=True, local_path=path)


def list_generation_files() -> list[dict[str, Any]]:
    with closing(db_connect()) as conn:
        rows = conn.execute("SELECT result_url, remote_url, local_path FROM generations").fetchall()
    return [dict(row) for row in rows]


def generation_metadata_by_filename() -> dict[str, dict[str, Any]]:
    """按生成文件名索引最近一次生成记录，供文件管理页展示来源。"""
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


# ── 管理后台账号 / 会话 ───────────────────────────────
def ensure_default_admin_user() -> None:
    """首次启动时创建默认管理员。密码只以哈希形式写入数据库。"""
    username = os.getenv("ADMIN_USERNAME", "admin").strip() or "admin"
    default_password = os.getenv("ADMIN_DEFAULT_PASSWORD", "admin123456")
    with closing(db_connect()) as conn:
        row = conn.execute("SELECT username FROM admin_users LIMIT 1").fetchone()
        if row is not None:
            return
        conn.execute(
            "INSERT INTO admin_users(username,password_hash,created_at,updated_at) VALUES(?,?,?,?)",
            (username, hash_password(default_password), now_iso(), now_iso()),
        )


def verify_admin_login(username: str, password: str) -> bool:
    with closing(db_connect()) as conn:
        row = conn.execute("SELECT password_hash FROM admin_users WHERE username = ?", (username,)).fetchone()
    if row is None:
        return False
    return verify_password(password, str(row["password_hash"]))


def create_admin_session(username: str, ttl_seconds: int = 7 * 24 * 3600) -> tuple[str, float]:
    token = generate_session_token()
    expires_at = time.time() + ttl_seconds
    with closing(db_connect()) as conn:
        conn.execute(
            "INSERT INTO admin_sessions(session_hash,username,expires_at,created_at) VALUES(?,?,?,?)",
            (hash_token(token), username, expires_at, now_iso()),
        )
    return token, expires_at


def get_admin_session(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    digest = hash_token(token)
    with closing(db_connect()) as conn:
        row = conn.execute("SELECT username,expires_at FROM admin_sessions WHERE session_hash = ?", (digest,)).fetchone()
    if row is None:
        return None
    if float(row["expires_at"]) < time.time():
        delete_admin_session(token)
        return None
    return {"username": str(row["username"]), "expires_at": float(row["expires_at"])}


def delete_admin_session(token: str) -> None:
    if not token:
        return
    with closing(db_connect()) as conn:
        conn.execute("DELETE FROM admin_sessions WHERE session_hash = ?", (hash_token(token),))


def purge_expired_admin_sessions() -> int:
    with closing(db_connect()) as conn:
        cur = conn.execute("DELETE FROM admin_sessions WHERE expires_at < ?", (time.time(),))
    return int(cur.rowcount or 0)


def purge_old_admin_login_attempts(max_age_seconds: int = 24 * 3600) -> int:
    cutoff = time.time() - max_age_seconds
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    with closing(db_connect()) as conn:
        cur = conn.execute(
            "DELETE FROM admin_login_attempts WHERE updated_at < ? OR locked_until < ?",
            (cutoff_iso, time.time() - max_age_seconds),
        )
    return int(cur.rowcount or 0)


def cleanup_admin_security_state() -> dict[str, int]:
    return {
        "expired_sessions": purge_expired_admin_sessions(),
        "old_login_attempts": purge_old_admin_login_attempts(),
    }


def login_attempt_key(username: str, client_ip: str) -> str:
    normalized_user = (username or "").strip().lower() or "unknown"
    normalized_ip = (client_ip or "unknown").strip()
    return f"{normalized_user}@{normalized_ip}"


def get_admin_login_lock(username: str, client_ip: str) -> float:
    key = login_attempt_key(username, client_ip)
    with closing(db_connect()) as conn:
        row = conn.execute("SELECT locked_until FROM admin_login_attempts WHERE attempt_key = ?", (key,)).fetchone()
    if row is None:
        return 0.0
    locked_until = float(row["locked_until"] or 0)
    if locked_until <= time.time():
        return 0.0
    return locked_until


class LoginAttemptResult(NamedTuple):
    fail_count: int
    locked_until: float


def record_admin_login_failure(username: str, client_ip: str, max_failures: int = 5, lock_seconds: int = 30) -> LoginAttemptResult:
    key = login_attempt_key(username, client_ip)
    now_ts = time.time()
    with closing(db_connect()) as conn:
        row = conn.execute("SELECT fail_count, locked_until FROM admin_login_attempts WHERE attempt_key = ?", (key,)).fetchone()
        if row is None:
            fail_count = 1
        else:
            fail_count = int(row["fail_count"] or 0) + 1
        locked_until = now_ts + lock_seconds if fail_count >= max_failures else 0.0
        conn.execute(
            "INSERT INTO admin_login_attempts(attempt_key,fail_count,locked_until,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(attempt_key) DO UPDATE SET fail_count=excluded.fail_count, locked_until=excluded.locked_until, updated_at=excluded.updated_at",
            (key, fail_count, locked_until, now_iso()),
        )
    return LoginAttemptResult(fail_count=fail_count, locked_until=locked_until)


def clear_admin_login_failures(username: str, client_ip: str) -> None:
    key = login_attempt_key(username, client_ip)
    with db_transaction() as conn:
        conn.execute("DELETE FROM admin_login_attempts WHERE attempt_key = ?", (key,))
        cutoff_iso = datetime.fromtimestamp(time.time() - 24 * 3600, tz=timezone.utc).isoformat()
        conn.execute("DELETE FROM admin_login_attempts WHERE updated_at < ?", (cutoff_iso,))


def change_admin_password(username: str, current_password: str, new_password: str) -> bool:
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    if not verify_admin_login(username, current_password):
        return False
    with db_transaction(immediate=True) as conn:
        conn.execute(
            "UPDATE admin_users SET password_hash = ?, updated_at = ? WHERE username = ?",
            (hash_password(new_password), now_iso(), username),
        )
        conn.execute("DELETE FROM admin_sessions WHERE username = ?", (username,))
    return True


def delete_generation_records_for_file(local_path: str, result_url: str = "") -> int:
    with closing(db_connect()) as conn:
        cur = conn.execute(
            "DELETE FROM generations WHERE local_path = ? OR result_url = ?",
            (local_path, result_url),
        )
    return int(cur.rowcount or 0)


def clear_generations_and_collect_files() -> list[str]:
    """单事务收集 local_path 并清空历史，避免计数受并发读写影响。"""
    with db_transaction(immediate=True) as conn:
        rows = conn.execute("SELECT local_path FROM generations WHERE local_path IS NOT NULL AND local_path != ''").fetchall()
        paths = [str(row["local_path"]) for row in rows]
        conn.execute("DELETE FROM generations")
        return paths


def known_generated_local_paths() -> set[str]:
    with closing(db_connect()) as conn:
        rows = conn.execute("SELECT local_path FROM generations WHERE local_path IS NOT NULL AND local_path != ''").fetchall()
    return {str(row["local_path"]) for row in rows}


# ── Job CRUD ───────────────────────────────────────────

_JOB_COLUMNS = (
    "id,kind,status,provider,model,prompt,input_json,output_json,"
    "error_code,error_message,external_task_id,"
    "created_at,updated_at,started_at,completed_at,duration_ms"
)


def create_job(
    *,
    kind: str,
    status: str = "queued",
    provider: str | None = None,
    model: str | None = None,
    prompt: str | None = None,
    input_json: str | None = None,
    output_json: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    external_task_id: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    """创建 job，返回完整 job dict。"""
    job_id = uuid.uuid4().hex
    now = now_iso()
    with closing(db_connect()) as conn:
        conn.execute(
            "INSERT INTO jobs("
            "id,kind,status,provider,model,prompt,input_json,output_json,"
            "error_code,error_message,external_task_id,"
            "created_at,updated_at,started_at,completed_at,duration_ms"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id, kind, status, provider, model, prompt,
                input_json, output_json, error_code, error_message,
                external_task_id, now, now, started_at, completed_at,
                duration_ms,
            ),
        )
    return get_job(job_id) or {}


def get_job(job_id: str) -> dict[str, Any] | None:
    """按 ID 查询单个 job，不存在返回 None。"""
    with closing(db_connect()) as conn:
        row = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def get_job_by_external_task_id(external_task_id: str, *, kind: str | None = None) -> dict[str, Any] | None:
    """按 external_task_id 查询最新 job，可选 kind 限定。不存在返回 None。"""
    if not external_task_id:
        return None
    if kind:
        sql = f"SELECT {_JOB_COLUMNS} FROM jobs WHERE external_task_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1"
        params = (external_task_id, kind)
    else:
        sql = f"SELECT {_JOB_COLUMNS} FROM jobs WHERE external_task_id = ? ORDER BY created_at DESC LIMIT 1"
        params = (external_task_id,)
    with closing(db_connect()) as conn:
        row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return dict(row)


def list_jobs(
    *,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """按 created_at DESC 列出 job，支持 kind/status 过滤。"""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    conditions: list[str] = []
    params: list[Any] = []
    if kind:
        conditions.append("kind = ?")
        params.append(kind)
    if status:
        conditions.append("status = ?")
        params.append(status)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT {_JOB_COLUMNS} FROM jobs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with closing(db_connect()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def update_job_status(
    job_id: str,
    *,
    status: str,
    provider: str | None = None,
    model: str | None = None,
    output_json: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    external_task_id: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any] | None:
    """更新 job 状态及相关字段，自动刷新 updated_at。"""
    existing = get_job(job_id)
    if existing is None:
        return None
    now = now_iso()
    new_provider = provider if provider is not None else existing.get("provider")
    new_model = model if model is not None else existing.get("model")
    new_output_json = output_json if output_json is not None else existing.get("output_json")
    new_error_code = error_code if error_code is not None else existing.get("error_code")
    new_error_message = error_message if error_message is not None else existing.get("error_message")
    new_external_task_id = external_task_id if external_task_id is not None else existing.get("external_task_id")
    new_started_at = started_at if started_at is not None else existing.get("started_at")
    new_completed_at = completed_at if completed_at is not None else existing.get("completed_at")
    new_duration_ms = duration_ms if duration_ms is not None else existing.get("duration_ms")
    with closing(db_connect()) as conn:
        conn.execute(
            "UPDATE jobs SET status=?,provider=?,model=?,output_json=?,"
            "error_code=?,error_message=?,external_task_id=?,"
            "started_at=?,completed_at=?,duration_ms=?,updated_at=? "
            "WHERE id=?",
            (
                status, new_provider, new_model, new_output_json,
                new_error_code, new_error_message, new_external_task_id,
                new_started_at, new_completed_at, new_duration_ms, now,
                job_id,
            ),
        )
    return get_job(job_id)


def fail_job(job_id: str, error_code: str, error_message: str) -> dict[str, Any] | None:
    """标记 job 为 failed。"""
    return update_job_status(job_id, status="failed", error_code=error_code, error_message=error_message)
