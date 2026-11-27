"""执行凭据只保存消费身份、连接绑定和任务意图摘要，不重复保存提案正文。"""
from __future__ import annotations

import json
import hmac

from ..helpers import now_iso
from ..job_submission import canonical_submission_json, submission_intent_hash
from ..media_execution_scope import MediaExecutionRejected

MARKER = 'assistant_action'


def read_binding_key(conn):
    row = conn.execute('SELECT value FROM assistant_action_binding_key WHERE id=1').fetchone()
    if row is None:
        raise RuntimeError('action_binding_key_unavailable')
    return bytes.fromhex(row['value'])


def job_intent_digest(job):
    fields = ('kind', 'provider', 'model', 'prompt', 'request_hash', 'request_hash_version', 'payload_schema_version')
    payload = json.loads(job['input_json'])
    facts = {field: job.get(field) for field in fields}
    # 新分阶段图片把实际执行路由作为运行事实，授权仍绑定最初批准的完整候选链。
    if job.get('kind') == 'image' and payload.get('async_image', {}).get('version') == 1:
        first = payload['route']['routes'][0]
        facts.update(provider=first['provider'], model=first['model'])
    return submission_intent_hash({**facts, 'payload': payload})


def save_grant(conn, action, candidate, job):
    bindings = candidate.body()['configuration_bindings']
    connections = [{'provider': item['provider'], 'model': item['model'], 'connection': item['connection']} for item in bindings]
    conn.execute('INSERT INTO assistant_action_execution_grants VALUES(?,?,?,?,?,?,?)',
        (job['id'], action['id'], action['version'], candidate.digest, job_intent_digest(job), canonical_submission_json(connections), now_iso()))


def load_grant(conn, job):
    grant = conn.execute('SELECT * FROM assistant_action_execution_grants WHERE job_id=?', (job['id'],)).fetchone()
    receipt = conn.execute('SELECT * FROM assistant_actions WHERE job_id=?', (job['id'],)).fetchone()
    payload = json.loads(job['input_json'])
    marker = payload.get(MARKER)
    if grant is None and receipt is None and marker is None:
        return None
    if grant is None or receipt is None or receipt['status'] != 'submitted':
        raise MediaExecutionRejected('action_execution_grant_missing')
    expected = {'id': grant['action_id'], 'version': grant['version'], 'digest': grant['binding_digest']}
    if marker != expected or receipt['id'] != grant['action_id'] or receipt['version'] != grant['version']:
        raise MediaExecutionRejected('action_execution_receipt_changed')
    if not hmac.compare_digest(job_intent_digest(job), grant['intent_digest']):
        raise MediaExecutionRejected('action_execution_payload_changed')
    return dict(grant)
