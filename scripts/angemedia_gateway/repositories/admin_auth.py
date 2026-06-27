"""Admin user, session, and login-attempt repository."""
from __future__ import annotations

import logging
import os
import re
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, NamedTuple

from fastapi import HTTPException

from ..db.connection import db_connect, db_transaction
from ..helpers import now_iso
from ..security import generate_session_token, hash_password, hash_token, verify_password

log = logging.getLogger("angemedia-gateway")

ADMIN_USERNAME_RE = re.compile(r"^[\w.@-]{3,64}$", re.UNICODE)


def validate_admin_username(username: str) -> str:
    normalized = (username or "").strip()
    if not ADMIN_USERNAME_RE.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="用户名需为 3-64 位，可包含中文、字母、数字、点、下划线、短横线或 @")
    return normalized


def validate_admin_password(password: str) -> str:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    return password


def ensure_default_admin_user() -> None:
    """Create the first admin user when the DB has no admin users."""

    username = os.getenv("ADMIN_USERNAME", "admin").strip() or "admin"
    with closing(db_connect()) as conn:
        row = conn.execute("SELECT username FROM admin_users LIMIT 1").fetchone()
        if row is not None:
            return
        default_password = (os.getenv("ADMIN_DEFAULT_PASSWORD") or "").strip()
        if not default_password:
            raise RuntimeError(
                "ADMIN_DEFAULT_PASSWORD is not set. "
                "Set this environment variable to initialise the admin password."
            )
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
    validate_admin_password(new_password)
    if not verify_admin_login(username, current_password):
        return False
    with db_transaction(immediate=True) as conn:
        conn.execute(
            "UPDATE admin_users SET password_hash = ?, updated_at = ? WHERE username = ?",
            (hash_password(new_password), now_iso(), username),
        )
        conn.execute("DELETE FROM admin_sessions WHERE username = ?", (username,))
    return True


def change_admin_username(username: str, current_password: str, new_username: str) -> bool:
    new_username = validate_admin_username(new_username)
    if not verify_admin_login(username, current_password):
        return False
    with db_transaction(immediate=True) as conn:
        if new_username != username:
            existing = conn.execute("SELECT 1 FROM admin_users WHERE username = ?", (new_username,)).fetchone()
            if existing is not None:
                raise HTTPException(status_code=400, detail="用户名已存在")
        conn.execute(
            "UPDATE admin_users SET username = ?, updated_at = ? WHERE username = ?",
            (new_username, now_iso(), username),
        )
        conn.execute("DELETE FROM admin_sessions WHERE username IN (?, ?)", (username, new_username))
    return True


def update_admin_account(
    username: str,
    *,
    current_password: str,
    new_username: str | None = None,
    new_password: str | None = None,
) -> dict[str, Any] | None:
    target_username = validate_admin_username(new_username) if new_username is not None else username
    target_password_hash = hash_password(validate_admin_password(new_password)) if new_password is not None else None
    if new_username is None and new_password is None:
        raise HTTPException(status_code=400, detail="至少需要提供新用户名或新密码")
    with db_transaction(immediate=True) as conn:
        row = conn.execute("SELECT password_hash FROM admin_users WHERE username = ?", (username,)).fetchone()
        if row is None or not verify_password(current_password, str(row["password_hash"])):
            return None
        if target_username != username:
            existing = conn.execute("SELECT 1 FROM admin_users WHERE username = ?", (target_username,)).fetchone()
            if existing is not None:
                raise HTTPException(status_code=400, detail="用户名已存在")
        if target_password_hash is None:
            conn.execute(
                "UPDATE admin_users SET username = ?, updated_at = ? WHERE username = ?",
                (target_username, now_iso(), username),
            )
        else:
            conn.execute(
                "UPDATE admin_users SET username = ?, password_hash = ?, updated_at = ? WHERE username = ?",
                (target_username, target_password_hash, now_iso(), username),
            )
        conn.execute("DELETE FROM admin_sessions WHERE username IN (?, ?)", (username, target_username))
    return {"username": target_username}
