"""服务端业务助手权限；从当前租约和数据库取得主体，不信任模型参数。"""
from __future__ import annotations

import asyncio
from contextlib import closing

from ..assistant_async import settle_on_cancel
from ..assistant_run_access import RunUnavailable
from ..db.connection import db_connect
from ..assistant_runtime_policy import admission_state


class AssistantPolicyDenied(RuntimeError):
    pass


def require_admission(conn) -> None:
    state = admission_state(conn)
    if state != "running":
        raise RunUnavailable(state)


def business_readiness(path) -> str:
    with closing(db_connect(path=path)) as conn:
        return admission_state(conn)


def authorize_business_sync(context) -> None:
    context.raise_if_stopped()
    with context.repository.write_guard(context.lease) as (conn, run):
        if not conn.execute(
            "SELECT 1 FROM assistant_session_owners o JOIN admin_users u ON u.username=o.username "
            "JOIN assistant_sessions s ON s.id=o.session_id WHERE s.id=? AND s.access_scope='account'",
            (run["session_id"],),
        ).fetchone():
            raise AssistantPolicyDenied("assistant_actor_unavailable")
        state = admission_state(conn)
        if state != "running":
            raise AssistantPolicyDenied(state)
    context.raise_if_stopped()


async def authorize_business(context) -> None:
    await settle_on_cancel(asyncio.to_thread(authorize_business_sync, context))
