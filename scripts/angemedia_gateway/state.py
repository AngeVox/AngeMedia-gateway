"""SQLite 本地状态库。"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from fastapi import HTTPException

from . import config as C
from .security import generate_session_token, hash_password, hash_token, validate_task_id, verify_password


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_connect() -> sqlite3.Connection:
    C.DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(C.DB_FILE, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_transaction(immediate: bool = False):
    """在 autocommit 连接上显式开启事务，统一多语句写入的提交/回滚语义。"""
    with closing(db_connect()) as conn:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


PROVIDER_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")

BUILTIN_PROVIDER_CONFIG_KEYS = {
    "siliconflow": "BUILTIN_PROVIDER_SILICONFLOW_ENABLED",
    "modelscope": "BUILTIN_PROVIDER_MODELSCOPE_ENABLED",
    "pollinations": "BUILTIN_PROVIDER_POLLINATIONS_ENABLED",
    "openai_image": "BUILTIN_PROVIDER_OPENAI_IMAGE_ENABLED",
    "agnes_image": "BUILTIN_PROVIDER_AGNES_IMAGE_ENABLED",
    "agnes_video": "BUILTIN_PROVIDER_AGNES_VIDEO_ENABLED",
}


def validate_provider_id(provider_id: str) -> str:
    value = provider_id.strip().lower()
    if not PROVIDER_ID_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="渠道 ID 只能包含小写字母、数字和连字符，长度 1-64")
    return value


def is_relative_to_path(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def safe_unlink_under(path_text: str, base_dir: Path) -> bool:
    if not path_text:
        return False
    path = Path(path_text).expanduser()
    resolved = path.resolve()
    base = base_dir.resolve()
    if not is_relative_to_path(resolved, base):
        raise HTTPException(status_code=400, detail="拒绝删除目录外文件")
    if resolved.exists() and resolved.is_file():
        try:
            resolved.unlink()
            return True
        except OSError:
            return False
    return False


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
                UNIQUE(storage_area, relative_path)
            );
            """
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
    }
    for table, columns in additions.items():
        existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def get_config(key: str, default: str = "") -> str:
    with closing(db_connect()) as conn:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    if row is None:
        return os.getenv(key, default)
    return str(row["value"])


def set_config(key: str, value: str) -> None:
    if key not in C.CONFIG_KEYS:
        raise HTTPException(status_code=400, detail=f"不支持的配置项：{key}")
    with closing(db_connect()) as conn:
        conn.execute(
            "INSERT INTO config(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, now_iso()),
        )


def set_config_many(settings: dict[str, str]) -> None:
    for key in settings:
        if key not in C.CONFIG_KEYS:
            raise HTTPException(status_code=400, detail=f"不支持的配置项：{key}")
    now = now_iso()
    with db_transaction() as conn:
        conn.executemany(
            "INSERT INTO config(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            [(key, str(value), now) for key, value in settings.items()],
        )


def load_saved_settings() -> dict[str, str]:
    with closing(db_connect()) as conn:
        rows = conn.execute("SELECT key,value FROM config").fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def apply_saved_config_to_runtime() -> None:
    C.update_runtime(load_saved_settings())


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def config_snapshot(mask: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in sorted(C.CONFIG_KEYS):
        value = get_config(key, "")
        out[key] = mask_secret(value) if mask and key in C.SECRET_KEYS else value
    return out


def builtin_provider_enabled(provider_id: str) -> bool:
    key = BUILTIN_PROVIDER_CONFIG_KEYS.get(provider_id)
    if not key:
        return True
    return get_config(key, "true").strip().lower() in {"1", "true", "yes", "on"}


def set_builtin_provider_enabled(provider_id: str, enabled: bool) -> None:
    key = BUILTIN_PROVIDER_CONFIG_KEYS.get(provider_id)
    if not key:
        raise HTTPException(status_code=404, detail="内置渠道不存在")
    set_config(key, "true" if enabled else "false")


def safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def first_result_url(result: dict[str, Any]) -> tuple[str, str, str]:
    if "video_url" in result:
        return str(result.get("video_url") or ""), str(result.get("remote_video_url") or ""), str(result.get("local_path") or "")
    data = result.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        item = data[0]
        return str(item.get("url") or ""), str(item.get("remote_url") or ""), str(item.get("local_path") or "")
    return "", "", ""


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
) -> None:
    """写入资产记录，(storage_area, relative_path) 冲突时更新 metadata，保留 created_at。"""
    now = now_iso()
    try:
        with closing(db_connect()) as conn:
            conn.execute(
                """
                INSERT INTO assets(
                    id, filename, storage_area, relative_path, url_path,
                    media_type, source, size, prompt, model, provider,
                    duration_ms, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(storage_area, relative_path) DO UPDATE SET
                    filename=excluded.filename,
                    url_path=excluded.url_path,
                    media_type=excluded.media_type,
                    source=excluded.source,
                    size=excluded.size,
                    prompt=excluded.prompt,
                    model=excluded.model,
                    provider=excluded.provider,
                    duration_ms=excluded.duration_ms
                """,
                (
                    id, filename, storage_area, relative_path, url_path,
                    media_type, source, size, prompt, model, provider,
                    duration_ms, now,
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


def list_assets(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    """按 created_at DESC 分页列出资产。"""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    with closing(db_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM assets ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
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


def list_custom_providers(include_secret: bool = False) -> list[dict[str, Any]]:
    with closing(db_connect()) as conn:
        rows = conn.execute("SELECT * FROM custom_providers ORDER BY sort_order ASC, created_at DESC").fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if not include_secret and item.get("api_key"):
            item["api_key"] = mask_secret(str(item["api_key"]))
        item["enabled"] = bool(item.get("enabled"))
        items.append(item)
    return items


def get_custom_provider(provider_id: str, include_secret: bool = True) -> dict[str, Any] | None:
    provider_id = validate_provider_id(provider_id)
    with closing(db_connect()) as conn:
        row = conn.execute("SELECT * FROM custom_providers WHERE id = ?", (provider_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["enabled"] = bool(item.get("enabled"))
    if not include_secret and item.get("api_key"):
        item["api_key"] = mask_secret(str(item["api_key"]))
    return item


def upsert_custom_provider(data: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(data.get("id") or "").strip()
    if not provider_id:
        provider_id = "custom-" + uuid.uuid4().hex[:12]
    provider_id = validate_provider_id(provider_id)
    name = str(data.get("name") or provider_id).strip()
    provider_type = str(data.get("provider_type") or data.get("type") or "openai_image").strip()
    if provider_type not in {"openai_image"}:
        raise HTTPException(status_code=400, detail="当前自定义渠道只支持 openai_image 兼容格式")
    base_url = str(data.get("base_url") or "").strip().rstrip("/")
    default_model = str(data.get("default_model") or data.get("model") or "").strip()
    if not base_url or not default_model:
        raise HTTPException(status_code=400, detail="base_url 和 default_model 必填")
    try:
        sort_order = int(data.get("sort_order", 100))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="渠道排序必须是整数") from exc
    api_key = str(data.get("api_key") or "").strip()
    enabled = 1 if str(data.get("enabled", "true")).lower() in {"1", "true", "yes", "on"} else 0
    status_url = str(data.get("status_url") or "").strip()
    quota_url = str(data.get("quota_url") or "").strip()
    notes = str(data.get("notes") or "").strip()
    existing = get_custom_provider(provider_id, include_secret=True)
    if not api_key and existing:
        api_key = str(existing.get("api_key") or "")
    now = now_iso()
    with closing(db_connect()) as conn:
        conn.execute(
            """
            INSERT INTO custom_providers(
                id,name,provider_type,base_url,api_key,default_model,enabled,status_url,quota_url,notes,sort_order,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                provider_type=excluded.provider_type,
                base_url=excluded.base_url,
                api_key=excluded.api_key,
                default_model=excluded.default_model,
                enabled=excluded.enabled,
                status_url=excluded.status_url,
                quota_url=excluded.quota_url,
                notes=excluded.notes,
                sort_order=excluded.sort_order,
                updated_at=excluded.updated_at
            """,
            (
                provider_id, name, provider_type, base_url, api_key, default_model, enabled,
                status_url, quota_url, notes, sort_order, now, now,
            ),
        )
    return get_custom_provider(provider_id, include_secret=False) or {}


def delete_custom_provider(provider_id: str) -> bool:
    provider_id = validate_provider_id(provider_id)
    with closing(db_connect()) as conn:
        cursor = conn.execute("DELETE FROM custom_providers WHERE id = ?", (provider_id,))
    return cursor.rowcount > 0


def update_custom_provider_enabled(provider_id: str, enabled: bool) -> dict[str, Any]:
    provider_id = validate_provider_id(provider_id)
    with closing(db_connect()) as conn:
        cursor = conn.execute(
            "UPDATE custom_providers SET enabled = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, now_iso(), provider_id),
        )
    if cursor.rowcount <= 0:
        raise HTTPException(status_code=404, detail="自定义渠道不存在")
    return get_custom_provider(provider_id, include_secret=False) or {}


def update_custom_provider_sort(provider_id: str, sort_order: int) -> dict[str, Any]:
    provider_id = validate_provider_id(provider_id)
    with closing(db_connect()) as conn:
        cursor = conn.execute(
            "UPDATE custom_providers SET sort_order = ?, updated_at = ? WHERE id = ?",
            (int(sort_order), now_iso(), provider_id),
        )
    if cursor.rowcount <= 0:
        raise HTTPException(status_code=404, detail="自定义渠道不存在")
    return get_custom_provider(provider_id, include_secret=False) or {}


def update_custom_provider_test(
    provider_id: str,
    status: str,
    response_ms: int = 0,
    error: str = "",
) -> dict[str, Any]:
    provider_id = validate_provider_id(provider_id)
    with closing(db_connect()) as conn:
        cursor = conn.execute(
            """
            UPDATE custom_providers
            SET last_test_at = ?, last_test_status = ?, last_response_ms = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (now_iso(), status, int(response_ms or 0), error[:1000], now_iso(), provider_id),
        )
    if cursor.rowcount <= 0:
        raise HTTPException(status_code=404, detail="自定义渠道不存在")
    return get_custom_provider(provider_id, include_secret=False) or {}


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
