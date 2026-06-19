"""Persistence for built-in provider runtime overrides."""
from __future__ import annotations

from contextlib import closing
import re
import sqlite3
from typing import Any

from ..db.connection import db_connect, db_transaction
from ..helpers import now_iso


_UNSET = object()
_PROVIDER_ID_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


def _provider_id(value: str) -> str:
    provider_id = str(value or "").strip()
    if not _PROVIDER_ID_RE.fullmatch(provider_id):
        raise ValueError("invalid provider id")
    return provider_id


def get_provider_runtime_config(provider_id: str) -> dict[str, Any] | None:
    provider_id = _provider_id(provider_id)
    try:
        with closing(db_connect()) as conn:
            row = conn.execute(
                "SELECT provider_id, enabled, api_key, base_url_override, default_model_override, updated_at "
                "FROM provider_runtime_configs WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return None
        raise
    if row is None:
        return None
    item = dict(row)
    if item.get("enabled") is not None:
        item["enabled"] = bool(item["enabled"])
    return item


def update_provider_runtime_config(
    provider_id: str,
    *,
    enabled: bool | None | object = _UNSET,
    api_key: str | None | object = _UNSET,
    base_url_override: str | None | object = _UNSET,
    default_model_override: str | None | object = _UNSET,
) -> dict[str, Any]:
    provider_id = _provider_id(provider_id)
    updates = {
        "enabled": enabled,
        "api_key": api_key,
        "base_url_override": base_url_override,
        "default_model_override": default_model_override,
    }
    updated_at = now_iso()
    with db_transaction(immediate=True) as conn:
        row = conn.execute(
            "SELECT enabled, api_key, base_url_override, default_model_override "
            "FROM provider_runtime_configs WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()
        existing = dict(row) if row is not None else {}
        values = {
            "enabled": existing.get("enabled"),
            "api_key": existing.get("api_key"),
            "base_url_override": existing.get("base_url_override"),
            "default_model_override": existing.get("default_model_override"),
        }
        for key, value in updates.items():
            if value is not _UNSET:
                values[key] = value
        conn.execute(
            """
            INSERT INTO provider_runtime_configs(
                provider_id, enabled, api_key, base_url_override, default_model_override, updated_at
            ) VALUES(?,?,?,?,?,?)
            ON CONFLICT(provider_id) DO UPDATE SET
                enabled=excluded.enabled,
                api_key=excluded.api_key,
                base_url_override=excluded.base_url_override,
                default_model_override=excluded.default_model_override,
                updated_at=excluded.updated_at
            """,
            (
                provider_id,
                None if values["enabled"] is None else (1 if values["enabled"] else 0),
                values["api_key"],
                values["base_url_override"],
                values["default_model_override"],
                updated_at,
            ),
        )
    return get_provider_runtime_config(provider_id) or {"provider_id": provider_id, "updated_at": updated_at}


def clear_provider_runtime_api_key(provider_id: str) -> dict[str, Any]:
    return update_provider_runtime_config(provider_id, api_key=None)
