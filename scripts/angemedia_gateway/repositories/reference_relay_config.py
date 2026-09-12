"""SQLite persistence for reference-image relay configuration."""
from __future__ import annotations

from contextlib import closing
from typing import Any

from ..db.connection import db_connect, db_transaction
from ..helpers import now_iso

_KEYS = {
    "mode": "REFERENCE_RELAY_MODE",
    "upload_url": "REFERENCE_RELAY_UPLOAD_URL",
    "token": "REFERENCE_RELAY_TOKEN",
}
_UNSET = object()


def get_reference_relay_config() -> dict[str, Any]:
    with closing(db_connect()) as conn:
        rows = conn.execute(
            "SELECT key, value FROM config WHERE key IN (?, ?, ?)",
            tuple(_KEYS.values()),
        ).fetchall()
    values = {str(row["key"]): str(row["value"]) for row in rows}
    return {name: values.get(key) or None for name, key in _KEYS.items()}


def update_reference_relay_config(
    *,
    mode: str | None | object = _UNSET,
    upload_url: str | None | object = _UNSET,
    token: str | None | object = _UNSET,
) -> dict[str, Any]:
    updates = (("mode", mode), ("upload_url", upload_url), ("token", token))
    now = now_iso()
    with db_transaction(immediate=True) as conn:
        for name, value in updates:
            if value is _UNSET:
                continue
            key = _KEYS[name]
            normalized = str(value or "").strip()
            if not normalized:
                conn.execute("DELETE FROM config WHERE key = ?", (key,))
                continue
            conn.execute(
                "INSERT INTO config(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, normalized, now),
            )
    return get_reference_relay_config()
