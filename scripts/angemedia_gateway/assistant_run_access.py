"""运行控制的请求主体；认证摘要仅用于数据库复核，不进入模型或公开结果。"""
from __future__ import annotations

from dataclasses import dataclass, field
import sqlite3
import time

from .repositories.assistant_conversations import require_conversation_owner


class RunAuthenticationExpired(PermissionError):
    pass


class RunUnavailable(RuntimeError):
    pass


class RunCursorInvalid(ValueError):
    pass


@dataclass(frozen=True)
class RunAccess:
    username: str
    session_hash: str = field(repr=False)


def authorize_admin_access(conn: sqlite3.Connection, access: RunAccess) -> None:
    """只验证当前登录；供归档后仍可读取的动作收据复用，不保存认证摘要。"""
    if not isinstance(access, RunAccess):
        raise RunAuthenticationExpired('管理会话已失效')
    if not conn.execute(
        "SELECT 1 FROM admin_sessions s JOIN admin_users u ON u.username=s.username "
        "WHERE s.session_hash=? AND s.username=? AND s.expires_at>?",
        (access.session_hash, access.username, time.time()),
    ).fetchone():
        raise RunAuthenticationExpired("管理会话已失效")


def authorize_run_access(conn: sqlite3.Connection, access: RunAccess, session_id: str) -> None:
    """登录状态、账户归属和后续操作共用事务，注销/改名后不能沿用旧授权。"""
    authorize_admin_access(conn, access)
    require_conversation_owner(conn, session_id, access.username)


def validate_event_cursor(after: int, limit: int) -> None:
    if type(after) is not int or not 0 <= after <= 9223372036854775807:
        raise RunCursorInvalid("事件游标必须是SQLite整数范围内的非负整数")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("事件数量必须是1到500的整数")
