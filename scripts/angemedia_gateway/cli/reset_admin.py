"""Reset the sole administrator account from the NAS package settings boundary."""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from .. import config as C
from ..db.connection import db_transaction
from ..helpers import now_iso
from ..repositories.admin_auth import validate_admin_password, validate_admin_username
from ..security import hash_password


def backup_admin_database() -> Path:
    if not C.DB_FILE.is_file():
        raise RuntimeError("AngeMedia database is missing")
    backup_dir = C.DB_FILE.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_dir, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / f"angemedia-before-admin-reset-{stamp}.db"
    with sqlite3.connect(C.DB_FILE) as source, sqlite3.connect(backup_path) as destination:
        source.backup(destination)
    os.chmod(backup_path, 0o600)
    return backup_path


def reset_admin_credentials(username: str, password: str) -> Path:
    try:
        safe_username = validate_admin_username(username)
        safe_password = validate_admin_password(password)
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc

    backup_path = backup_admin_database()
    with db_transaction(immediate=True) as conn:
        rows = conn.execute("SELECT username FROM admin_users ORDER BY created_at").fetchall()
        if len(rows) > 1:
            raise RuntimeError("administrator reset requires exactly one existing administrator")
        password_hash = hash_password(safe_password)
        timestamp = now_iso()
        if rows:
            old_username = str(rows[0]["username"])
            if safe_username != old_username:
                conflict = conn.execute(
                    "SELECT 1 FROM admin_users WHERE username = ?", (safe_username,)
                ).fetchone()
                if conflict is not None:
                    raise RuntimeError("administrator username already exists")
            conn.execute(
                "UPDATE admin_users SET username = ?, password_hash = ?, updated_at = ? WHERE username = ?",
                (safe_username, password_hash, timestamp, old_username),
            )
        else:
            conn.execute(
                "INSERT INTO admin_users(username,password_hash,created_at,updated_at) VALUES(?,?,?,?)",
                (safe_username, password_hash, timestamp, timestamp),
            )
        conn.execute("DELETE FROM admin_sessions")
        conn.execute("DELETE FROM admin_login_attempts")
    return backup_path


def _read_credentials() -> tuple[str, str]:
    parts = sys.stdin.buffer.read().split(b"\0")
    if len(parts) != 3 or parts[-1] != b"":
        raise ValueError("credential input must contain two NUL-terminated values")
    return parts[0].decode("utf-8"), parts[1].decode("utf-8")


def main() -> int:
    try:
        username, password = _read_credentials()
        backup_path = reset_admin_credentials(username, password)
    except Exception as exc:
        print(f"administrator reset failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"administrator credentials reset; backup={backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
