"""人类登录与后台提案权限分开；后台只持租约，不能取得确认能力。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .assistant_action_contract import ActionError
from .assistant_run_access import RunAccess, authorize_admin_access, authorize_run_access
from .assistant_runtime_contract import RunLease
from .assistant_runtime_policy import admission_state


@dataclass(frozen=True)
class ProposalRunAccess:
    repository: object = field(repr=False)
    lease: RunLease = field(repr=False)


def authorize_action_access(conn, access, session_id=None):
    if isinstance(access, RunAccess):
        authorize_admin_access(conn, access) if session_id is None else authorize_run_access(conn, access, session_id)
        return access.username
    if not isinstance(access, ProposalRunAccess) or not conn.in_transaction:
        raise ActionError('action_actor_unavailable')
    database = conn.execute('PRAGMA database_list').fetchone()['file']
    if Path(database).resolve() != access.repository.db_path:
        raise ActionError('action_actor_unavailable')
    run = access.repository.require_execution(conn, access.lease)
    if session_id is not None and run['session_id'] != session_id:
        raise ActionError('action_not_found')
    row = conn.execute('SELECT o.username FROM assistant_session_owners o JOIN admin_users u ON u.username=o.username '
                       'WHERE o.session_id=?', (run['session_id'],)).fetchone()
    if row is None or admission_state(conn) != 'running':
        raise ActionError('action_actor_unavailable')
    return row['username']


def require_human_access(access):
    if not isinstance(access, RunAccess):
        raise ActionError('human_confirmation_required')
