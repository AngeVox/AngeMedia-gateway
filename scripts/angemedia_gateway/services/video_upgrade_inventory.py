"""升级前只读盘点持久任务证据；不初始化数据库，不探测渠道，不授予历史来源。"""
from collections import Counter
import hashlib
import hmac
import json
import re

from ..db.connection import readonly_snapshot
from ..repositories.assistant_action_execution import job_intent_digest, load_grant
from ..repositories.video_job_origins import external_task_digest, get_video_job_origin
from .job_control import recovery_state
from .legacy_job_evidence import has_legacy_submission_rejection
from .video_job_admission import is_worker_managed_video_job
from .video_polling import COMPLETED_PROVIDER_STATUSES, FAILED_PROVIDER_STATUSES

_ID = re.compile(r'^[a-f0-9]{32}$')
_DIGEST = re.compile(r'^[a-f0-9]{64}$')
_STATES = {'queued', 'running', 'succeeded', 'failed', 'canceled'}
_EVIDENCE_TABLES = {'job_dispatches', 'job_attempts', 'assistant_actions', 'job_submission_keys',
                    'assistant_action_execution_grants', 'video_job_origins'}


def job_reference(value):
    """只公开正常任务身份；不合规标识仅输出摘要。"""
    value = str(value or '')
    return value if _ID.fullmatch(value) else 'invalid-id:' + hashlib.sha256(value.encode()).hexdigest()


def _source(conn, job, tables):
    """只核对持久关联与摘要；当前连接、授权政策与引用文件仍由真正执行入口验证。"""
    try:
        payload = json.loads(job.get('input_json') or '{}')
        marked = isinstance(payload, dict) and payload.get('assistant_action') is not None
        linked = 'assistant_actions' in tables and conn.execute(
            'SELECT 1 FROM assistant_actions WHERE job_id=?', (job['id'],)).fetchone() is not None
        grant = None
        if marked or linked or ('assistant_action_execution_grants' in tables and conn.execute(
                'SELECT 1 FROM assistant_action_execution_grants WHERE job_id=?', (job['id'],)).fetchone()):
            if not {'assistant_actions', 'assistant_action_execution_grants'} <= tables:
                return 'invalid_assistant_grant'
            grant = load_grant(conn, {**job, 'input_json': job.get('input_json') or '{}'})
            if grant is None:
                return 'invalid_assistant_grant'
        origin = get_video_job_origin(job['id'], conn=conn) if 'video_job_origins' in tables else None
        if origin is not None:
            if (origin['provider'] != 'agnes_video' or origin['model'] != job.get('model')
                    or not _DIGEST.fullmatch(str(origin['connection_digest']))
                    or not hmac.compare_digest(origin['intent_digest'], job_intent_digest(job))):
                return 'invalid_execution_origin'
            task = job.get('external_task_id')
            if task:
                if not origin['submitted_at'] or origin['external_task_digest'] != external_task_digest(task):
                    return 'invalid_execution_origin'
            elif origin['external_task_digest'] is not None or origin['submitted_at'] is not None:
                return 'invalid_execution_origin'
            return 'recorded_execution'
        return 'linked_assistant_grant' if grant else 'unverified'
    except (KeyError, TypeError, ValueError, RuntimeError):
        return 'invalid_source_evidence'


def _job_reason(conn, job, tables):
    state = recovery_state(job)
    worker = is_worker_managed_video_job(job, conn=conn, available_tables=tables)
    source = _source(conn, job, tables)
    if job['status'] not in _STATES:
        return 'unknown_job_state', True, worker, source
    # 完整来源不修复未知提交，也不代表已失败/撤销的远端任务结束。
    if state == 'submission_unresolved' and source == 'unverified' and has_legacy_submission_rejection(conn, job):
        return 'recorded_submission_rejection', False, worker, source
    if state in {'submission_unresolved', 'existing_task_unresolved', 'state_unavailable'}:
        return state, True, worker, source
    if job['status'] not in {'queued', 'running'}:
        return 'terminal_local_record', False, worker, source
    if job.get('provider') != 'agnes_video':
        return 'unsupported_active_provider', True, worker, source
    if source.startswith('invalid_'):
        return source, True, worker, source
    if job.get('external_task_id'):
        if source == 'unverified':
            return 'unverified_queued_task' if worker else 'unverified_legacy_task', True, worker, source
        return 'original_connection_required', False, worker, source
    attempted = ('job_attempts' in tables and conn.execute(
        'SELECT 1 FROM job_attempts WHERE job_id=? LIMIT 1', (job['id'],)).fetchone() is not None)
    if (job.get('started_at') or job.get('attempt_count') or attempted or source == 'recorded_execution'
            or job['status'] == 'running'):
        return 'submission_outcome_unknown', True, worker, source
    if not worker or job.get('stage') != 'video_submit':
        return 'unrecognized_pending_submission', True, worker, source
    return 'pending_first_submission', False, worker, source


def scan_video_upgrade(conn, detail_limit):
    """在调用方的只读事务中复用原视频分类，不另开连接或改变快照。"""
    if not conn.in_transaction or conn.execute('PRAGMA query_only').fetchone()[0] != 1:
        raise ValueError('video_inventory_requires_readonly_snapshot')
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    columns = {row[1] for row in conn.execute('PRAGMA table_info(jobs)')}
    if not {'id', 'kind', 'status', 'provider', 'external_task_id'} <= columns:
        raise ValueError('video_inventory_schema_unsupported')
    counts, blocked_details, other_details, findings, blockers = Counter(), [], [], 0, 0

    def finding(reason, blocked, **facts):
        nonlocal findings, blockers
        findings += 1; blockers += int(blocked); counts[reason] += 1
        target = blocked_details if blocked else other_details
        if len(target) < detail_limit:
            target.append({'reason': reason, 'blocks_task_gate': blocked, **facts})

    video_jobs = 0
    for row in conn.execute("SELECT * FROM jobs WHERE kind='video' ORDER BY id"):
        job = dict(row); video_jobs += 1
        reason, blocked, worker, source = _job_reason(conn, job, tables)
        finding(reason, blocked, job_id=job_reference(job['id']),
                status=job['status'] if job['status'] in _STATES else 'unknown',
                worker_managed=worker, source_evidence=source, external_task_present=bool(job.get('external_task_id')))
    # 与兼容GET一致，仅在video类型内识别同号冲突；摘要不泄露外部编号。
    for row in conn.execute("SELECT external_task_id,count(*) AS n FROM jobs WHERE kind='video' "
                            "AND external_task_id IS NOT NULL AND external_task_id<>'' "
                            'GROUP BY external_task_id HAVING count(*)>1 ORDER BY external_task_id'):
        finding('ambiguous_external_identity', True,
                external_id_digest=hashlib.sha256(str(row[0]).encode()).hexdigest(), matching_jobs=row[1])
    cached_only = 0
    if 'video_tasks' in tables:
        for row in conn.execute("SELECT task_id,status FROM video_tasks WHERE NOT EXISTS "
                               "(SELECT 1 FROM jobs WHERE kind='video' AND external_task_id=video_tasks.task_id) ORDER BY task_id"):
            cached_only += 1
            terminal = str(row['status'] or '').lower() in COMPLETED_PROVIDER_STATUSES | FAILED_PROVIDER_STATUSES
            finding('cached_only_terminal_record' if terminal else 'cached_only_unresolved_task', not terminal,
                    external_id_digest=hashlib.sha256(str(row['task_id']).encode()).hexdigest())
    orphaned_origins = conn.execute('SELECT count(*) FROM video_job_origins WHERE NOT EXISTS '
        '(SELECT 1 FROM jobs WHERE id=video_job_origins.job_id)').fetchone()[0] if 'video_job_origins' in tables else 0
    if 'video_job_origins' in tables:
        for row in conn.execute('SELECT job_id FROM video_job_origins WHERE external_task_digest IS NULL '
                                'AND NOT EXISTS(SELECT 1 FROM jobs WHERE id=video_job_origins.job_id) ORDER BY job_id'):
            finding('orphaned_unbound_attempt', True, job_id=job_reference(row[0]))
    details = (blocked_details + other_details)[:detail_limit]
    return {'schema_version': 1, 'scope': 'local_video_task_evidence', 'scan_complete': True,
            'task_gate': 'blocked' if blockers else 'no_recorded_blockers', 'deployment_ready': False,
            'video_jobs': video_jobs, 'cached_only_records': cached_only, 'retained_origins_without_job': orphaned_origins,
            'blocker_count': blockers, 'reason_counts': dict(sorted(counts.items())),
            'findings_total': findings, 'details_omitted': findings - len(details), 'details': details,
            'missing_evidence_tables': sorted(_EVIDENCE_TABLES - tables),
            'not_verified': ['ingress_and_workers_drained', 'original_connection_available', 'provider_task_outcome',
                             'backup_restore', 'package_platform_compatibility', 'legacy_sync_origin_admission']}


def inventory_video_upgrade(database, *, detail_limit=1000):
    """一个只读事务包含完整盘点；明细上限不截断计数或放宽结论。"""
    if type(detail_limit) is not int or not 0 <= detail_limit <= 10000:
        raise ValueError('video_inventory_detail_limit_invalid')
    with readonly_snapshot(database) as conn:
        return scan_video_upgrade(conn, detail_limit)
