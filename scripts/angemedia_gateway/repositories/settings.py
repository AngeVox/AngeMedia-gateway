"""Settings / runtime config / custom provider 相关 DB helper。"""
from __future__ import annotations

import os
import uuid
from contextlib import closing
from typing import Any

from fastapi import HTTPException

from .. import config as C
from ..db.connection import db_connect, db_transaction
from ..helpers import now_iso, validate_provider_id


BUILTIN_PROVIDER_CONFIG_KEYS = {
    "siliconflow": "BUILTIN_PROVIDER_SILICONFLOW_ENABLED",
    "modelscope": "BUILTIN_PROVIDER_MODELSCOPE_ENABLED",
    "pollinations": "BUILTIN_PROVIDER_POLLINATIONS_ENABLED",
    "openai_image": "BUILTIN_PROVIDER_OPENAI_IMAGE_ENABLED",
    "agnes_image": "BUILTIN_PROVIDER_AGNES_IMAGE_ENABLED",
    "agnes_video": "BUILTIN_PROVIDER_AGNES_VIDEO_ENABLED",
}


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
    default = "false" if provider_id == "pollinations" else "true"
    return get_config(key, default).strip().lower() in {"1", "true", "yes", "on"}


def set_builtin_provider_enabled(provider_id: str, enabled: bool) -> None:
    key = BUILTIN_PROVIDER_CONFIG_KEYS.get(provider_id)
    if not key:
        raise HTTPException(status_code=404, detail="内置渠道不存在")
    set_config(key, "true" if enabled else "false")


# ── Custom provider CRUD ──────────────────────────────

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


def update_custom_provider_details(provider_id: str, data: dict[str, Any]) -> dict[str, Any]:
    provider_id = validate_provider_id(provider_id)
    existing = get_custom_provider(provider_id, include_secret=True)
    if existing is None:
        raise HTTPException(status_code=404, detail="自定义渠道不存在")

    name = str(data.get("name", existing.get("name") or provider_id)).strip()
    base_url = str(data.get("base_url", existing.get("base_url") or "")).strip().rstrip("/")
    default_model = str(data.get("default_model", existing.get("default_model") or "")).strip()
    notes = str(data.get("notes", existing.get("notes") or "") or "").strip()
    enabled_value = data.get("enabled", existing.get("enabled"))
    enabled = 1 if str(enabled_value).lower() in {"1", "true", "yes", "on"} else 0
    api_key = str(data.get("api_key") or "").strip()
    if not api_key:
        api_key = str(existing.get("api_key") or "")
    if not name:
        raise ValueError("name is required")
    if not base_url or not default_model:
        raise ValueError("base_url and default_model are required")

    with closing(db_connect()) as conn:
        cursor = conn.execute(
            """
            UPDATE custom_providers
            SET name = ?, base_url = ?, api_key = ?, default_model = ?, enabled = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (name, base_url, api_key, default_model, enabled, notes, now_iso(), provider_id),
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
