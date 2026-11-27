"""公开动作只呈现有效参数、能力/费用说明及任务引用；不输出存储载荷或认证信息。"""
from __future__ import annotations

import json

from ..repositories.jobs import get_job
from ..presenters.assistant_jobs import assistant_job_observation


def action_view(conn, action, revision, now_ms):
    status = action['status']
    if status == 'draft' and revision['expires_ms'] <= now_ms: status = 'expired'
    if status == 'draft' and revision['body_json'] is None: status = 'revoked'
    body = json.loads(revision['body_json']) if revision['body_json'] else None
    job = get_job(action['job_id'], conn=conn) if action['job_id'] else None
    return {'id': action['id'], 'session_id': action['session_id'], 'status': status,
            'version': revision['version'], 'current_version': action['version'],
            'binding_digest': revision['binding_digest'], 'expires_ms': revision['expires_ms'],
            'review': body['review'] if body else None, 'created_at': action['created_at'], 'updated_at': action['updated_at'],
            'job': (assistant_job_observation(job) if job else {'id': action['job_id'], 'status': 'record_unavailable'}) if action['job_id'] else None,
            'confirmed_at': action['confirmed_at'],
            'confirmed_acknowledgements': json.loads(action['confirmed_acks_json']) if action['confirmed_acks_json'] else []}
