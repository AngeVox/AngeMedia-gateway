"""从同一数据库快照取得旧任务审阅和升级盘点的原始证据。"""
import json
import re
import sqlite3

from ..job_submission import submission_intent_hash
from .job_control import recovery_state

_JOB_FIELDS = (
    'id, kind, status, stage, provider, model, external_task_id, input_json, output_json, '
    'error_code, error_message, error_category, provider_status, attempt_count, created_at, completed_at'
)
# 每组用持久主键排序。摘要版本与取证字段一同维护，新增无关列不改变老审阅。
_RELATED = {
    'job_attempts': ('id, attempt_number, stage, status, error_code, error_message, detail_json', 'attempt_number'),
    'job_events': ('id, event_type, to_status, stage, payload_json', 'id'),
    'job_dispatches': ('id, status, topic, payload_json', 'id'),
    'image_task_receipts': ('route_index, provider, model, intent_digest, connection_digest, state, task_id, provider_status', 'route_index'),
    'video_job_origins': ('provider, model, intent_digest, connection_digest, external_task_digest, submitted_at', 'job_id'),
    'assistant_action_execution_grants': ('action_id, version, binding_digest, intent_digest', 'job_id'),
    'assets': ('id, source, storage_area, relative_path', 'id'),
    'generations': ('id, status, task_id, result_url', 'id'),
}
_IMAGE_PREFIX = {
    'agnes_image': 'Agnes Image generate',
    'modelscope': 'ModelScope submit',
}
_HTTP_REJECTION = re.compile(
    r'(?:(?P<provider>agnes_image|modelscope)/[A-Za-z0-9_.\-/]{1,160}: )?'
    r'(?P<operation>Agnes Image generate|ModelScope submit|agnes_video submit) failed: '
    r'HTTP (?P<status>400|401|403|422|429) (?P<category>auth|validation|rate_limit)'
)
_CATEGORIES = {400: 'validation', 401: 'auth', 403: 'auth', 422: 'validation', 429: 'rate_limit'}


class LegacyReviewError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def _object(raw):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _recorded_rejection(job, related):
    attempts = related['job_attempts']
    if (len(attempts) != 1 or job['attempt_count'] not in (0, 1) or job['external_task_id']
            or job['output_json'] or any(row['status'] == 'publishing' for row in related['job_dispatches'])):
        return None
    if any(related[table] for table in ('image_task_receipts', 'video_job_origins',
                                       'assistant_action_execution_grants', 'assets', 'generations')):
        return None
    attempt = attempts[0]
    code, message = job['error_code'], job['error_message']
    if (attempt['attempt_number'], attempt['status'], attempt['error_code'], attempt['error_message']) != (1, 'failed', code, message):
        return None
    for event in related['job_events']:
        info = _object(event['payload_json'])
        if (event['event_type'] == 'worker_attempt_failed' and event['to_status'] == 'failed'
                and event['stage'] in {attempt['stage'], 'finalize'} and info.get('error_code') == code
                and type(info.get('attempt')) is int and info['attempt'] == attempt['attempt_number']):
            break
    else:
        return None
    provider = job['provider']
    if job['kind'] == 'image':
        expected = _IMAGE_PREFIX.get(provider)
        routing = _object(job['input_json']).get('route')
        routes = routing.get('routes') if isinstance(routing, dict) else None
        if (expected is None or (attempt['stage'], code) != ('image_generate', 'image_provider_failure')
                or not isinstance(routes, list) or len(routes) != 1 or not isinstance(routes[0], dict)
                or routes[0].get('provider') != provider):
            return None
        if provider == 'modelscope' and message in {
                'ModelScope remote quota is exhausted',
                f"modelscope/{job['model']}: ModelScope remote quota is exhausted",
        }:
            return 429
    else:
        if (provider, attempt['stage'], code) != ('agnes_video', 'video_submit', 'video_submit_ambiguous'):
            return None
        expected = 'agnes_video submit'
    match = _HTTP_REJECTION.fullmatch(message or '')
    if match and match['operation'] == expected and match['provider'] in {None, provider}:
        status = int(match['status'])
        if match['category'] == _CATEGORIES[status]:
            return status
    return None


def _related_facts(conn, job_id):
    return {
        table: [dict(row) for row in conn.execute(
            f'SELECT {fields} FROM {table} WHERE job_id=? ORDER BY {order}', (job_id,))]
        for table, (fields, order) in _RELATED.items()
    }


def has_legacy_submission_rejection(conn, job):
    """同一盘点快照中的原提交拒绝，只用于已失败任务的阻塞分类。"""
    if (job['status'] != 'failed' or job.get('stage') != 'finalize'
            or job.get('payload_schema_version') != 1
            or any(job.get(field) for field in ('provider_status', 'claim_token', 'claim_expires_at', 'next_retry_at'))):
        return False
    try:
        raw = job.get('input_json')
        payload = json.loads(raw) if raw is not None else {}
        if not isinstance(payload, dict):
            return False
        revision = payload.get('schema_version', 1)
        if (type(revision) is not int or revision != 1
                or payload.get('assistant_action') is not None or payload.get('async_image') is not None):
            return False
        if conn.execute('SELECT 1 FROM assistant_actions WHERE job_id=? LIMIT 1', (job['id'],)).fetchone():
            return False
        related = _related_facts(conn, job['id'])
        if _recorded_rejection(job, related) is None:
            return False
        # 后续阶段、成功事件或新的投递迹象与首次提交被拒绝不相容。
        submit_stage = related['job_attempts'][0]['stage']
        if any(event['to_status'] == 'succeeded' or event['stage'] not in {None, 'admitted', submit_stage, 'finalize'}
               for event in related['job_events']):
            return False
        return all(dispatch['status'] in {'canceled', 'failed'} or (
            dispatch['status'] in {'pending', 'published'}
            and _object(dispatch['payload_json']).get('stage') == submit_stage
        ) for dispatch in related['job_dispatches'])
    except (sqlite3.DatabaseError, TypeError, ValueError):
        # 证据表缺失或载荷损坏时保留原阻塞。
        return False


def collect_legacy_job_evidence(conn, job_id):
    """捕获证据时只返回摘要和来源类型。原始正文、错误和编号留在原表。"""
    try:
        row = conn.execute(f'SELECT {_JOB_FIELDS} FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise LegacyReviewError('job_unavailable', 404)
        job = dict(row)
        if (job['kind'] not in {'image', 'video'}
                or recovery_state(job) not in {'submission_unresolved', 'existing_task_unresolved'}):
            raise LegacyReviewError('job_not_reviewable')
        related = _related_facts(conn, job_id)
        if job['kind'] == 'video' and job['external_task_id']:
            task = conn.execute('SELECT status FROM video_tasks WHERE task_id=?', (job['external_task_id'],)).fetchone()
            related['video_task_status'] = task['status'] if task else None
        digest = submission_intent_hash({'version': 1, 'job': job, 'related': related})
        status = _recorded_rejection(job, related)
    except (sqlite3.DatabaseError, TypeError, UnicodeError) as exc:
        # 损坏或老结构可能没有采集必需字段，原预检仍由其自己的合同判定。
        raise LegacyReviewError('job_evidence_unavailable') from exc
    return {'version': 1, 'source': 'local_job_records', 'task_digest': digest, 'http_status': status,
            'conclusion': 'rejection_reviewed' if status is not None else 'needs_evidence'}
