"""动作和不可变修订的SQL边界；调用方持有授权事务，不在这里调用模型或供应商。"""
from __future__ import annotations

from ..assistant_action_contract import ActionError
from ..assistant_action_access import authorize_action_access, ProposalRunAccess
from ..helpers import now_iso
from ..job_submission import canonical_submission_json


def owned_action(conn, action_id, access):
    username = authorize_action_access(conn, access)
    row = conn.execute('SELECT * FROM assistant_actions WHERE id=? AND username=?', (action_id, username)).fetchone()
    if row is None: raise ActionError('action_not_found')
    if isinstance(access, ProposalRunAccess) and row['origin_session_id'] != access.lease.session_id:
        raise ActionError('action_not_found')
    return dict(row)


def creation(conn, session_id, request_key):
    row = conn.execute('SELECT * FROM assistant_actions WHERE origin_session_id=? AND creation_key=?', (session_id, request_key)).fetchone()
    return dict(row) if row else None


def revision(conn, action_id, version):
    row = conn.execute('SELECT * FROM assistant_action_revisions WHERE action_id=? AND version=?', (action_id, version)).fetchone()
    if row is None: raise ActionError('action_revision_unavailable')
    return dict(row)


def revision_request(conn, action_id, request_key):
    row = conn.execute('SELECT * FROM assistant_action_revisions WHERE action_id=? AND request_key=?', (action_id, request_key)).fetchone()
    return dict(row) if row else None


def create(conn, action_id, session_id, username, key, fingerprint):
    timestamp = now_iso()
    conn.execute("INSERT INTO assistant_actions(id,session_id,origin_session_id,username,creation_key,creation_fingerprint,version,status,created_at,updated_at) "
                 "VALUES(?,?,?,?,?,?,1,'draft',?,?)", (action_id, session_id, session_id, username, key, fingerprint, timestamp, timestamp))


def append_revision(conn, action, *, key, fingerprint, candidate, expires_ms):
    timestamp = now_iso(); version = action['version']
    conn.execute('INSERT INTO assistant_action_revisions(action_id,version,request_key,request_fingerprint,binding_digest,body_json,expires_ms,created_at) VALUES(?,?,?,?,?,?,?,?)',
                 (action['id'], version, key, fingerprint, candidate.digest, canonical_submission_json(candidate.body()), expires_ms, timestamp))
    conn.execute('UPDATE assistant_actions SET version=?,updated_at=? WHERE id=?', (version, timestamp, action['id']))
    conn.execute("UPDATE assistant_sessions SET updated_at=? WHERE id=? AND status='active'", (timestamp, action['session_id']))


def submit(conn, action_id, job_id, acknowledgements):
    timestamp = now_iso()
    conn.execute("UPDATE assistant_actions SET status='submitted',job_id=?,confirmed_at=?,confirmed_acks_json=?,updated_at=? WHERE id=? AND status='draft'",
                 (job_id, timestamp, canonical_submission_json(sorted(acknowledgements)), timestamp, action_id))


def revoke(conn, action_id):
    conn.execute("UPDATE assistant_actions SET status='revoked',updated_at=? WHERE id=? AND status='draft'", (now_iso(), action_id))
    conn.execute('UPDATE assistant_action_revisions SET body_json=NULL WHERE action_id=?', (action_id,))


def active_count(conn, username, now_ms):
    return conn.execute("SELECT count(*) FROM assistant_actions a JOIN assistant_action_revisions r ON r.action_id=a.id AND r.version=a.version "
                        "JOIN assistant_sessions s ON s.id=a.session_id WHERE a.username=? AND a.status='draft' AND s.status='active' "
                        "AND r.expires_ms>? AND r.body_json IS NOT NULL", (username, now_ms)).fetchone()[0]
