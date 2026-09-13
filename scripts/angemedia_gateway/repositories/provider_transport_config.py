"""SQLite persistence for explicit Provider transport configuration."""
from __future__ import annotations

from contextlib import closing
import re
from typing import Any

from ..db.connection import db_connect, db_transaction
from ..helpers import now_iso


_UNSET = object()
_PROVIDER_ID_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
_PREFIX = "PROVIDER_TRANSPORT"


def _scope(provider_id: str | None) -> str:
    if provider_id is None:
        return "GLOBAL"
    value = str(provider_id or "").strip().lower()
    if not _PROVIDER_ID_RE.fullmatch(value):
        raise ValueError("invalid provider id")
    return f"PROVIDER_{value}"


def _keys(provider_id: str | None) -> tuple[str, str]:
    scope = _scope(provider_id)
    return f"{_PREFIX}_{scope}_MODE", f"{_PREFIX}_{scope}_PROXY_URL"


def get_provider_transport_config(provider_id: str | None = None) -> dict[str, Any]:
    mode_key, proxy_key = _keys(provider_id)
    with closing(db_connect()) as conn:
        rows = conn.execute(
            "SELECT key, value FROM config WHERE key IN (?, ?)",
            (mode_key, proxy_key),
        ).fetchall()
    values = {str(row["key"]): str(row["value"]) for row in rows}
    return {
        "transport_mode": values.get(mode_key) or None,
        "proxy_url": values.get(proxy_key) or None,
    }


def update_provider_transport_config(
    provider_id: str | None = None,
    *,
    transport_mode: str | None | object = _UNSET,
    proxy_url: str | None | object = _UNSET,
) -> dict[str, Any]:
    mode_key, proxy_key = _keys(provider_id)
    updates = ((mode_key, transport_mode), (proxy_key, proxy_url))
    now = now_iso()
    with db_transaction(immediate=True) as conn:
        for key, value in updates:
            if value is _UNSET:
                continue
            normalized = str(value or "").strip()
            if not normalized:
                conn.execute("DELETE FROM config WHERE key = ?", (key,))
                continue
            conn.execute(
                "INSERT INTO config(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, normalized, now),
            )
    return get_provider_transport_config(provider_id)
