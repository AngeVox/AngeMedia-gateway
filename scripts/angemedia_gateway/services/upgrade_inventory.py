"""应用升级只读总览；同一主库快照复用视频分类，不探测渠道或执行恢复。"""
from collections import Counter
import hashlib
import json
import re

from ..assistant_runtime_contract import ACTIVE_RUN_STATUSES, TERMINAL_RUN_STATUSES
from ..db.connection import readonly_snapshot
from ..db.legacy_job_reviews import LEGACY_JOB_REVIEWS_VERSION
from ..db.migrations import BASE_SCHEMA_VERSIONS, MIGRATIONS
from ..job_submission import submission_intent_hash
from ..media_execution_scope import MediaExecutionRejected
from ..queue.messages import EXECUTABLE_STAGES, JobStageMessage
from ..queue.settings import WORKER_TASK_NAME
from ..repositories.assistant_action_execution import job_intent_digest, load_grant
from ..repositories.image_task_receipts import image_task_receipts
from ..repositories.jobs import JOB_STAGES_BY_KIND, VALID_JOB_STAGES, VALID_JOB_STATUSES
from .job_control import recovery_state
from .legacy_job_evidence import has_legacy_submission_rejection
from .legacy_job_reviews import scan_legacy_job_reviews
from .video_upgrade_inventory import job_reference, scan_video_upgrade

_DIGEST = re.compile(r'^[a-f0-9]{64}$')
# 这里只声明盘点实际读取的列，不冒充完整约束、迁移或目标版本兼容性验证。
_TABLES = {
    'jobs': {'id', 'kind', 'status', 'stage', 'provider', 'model', 'external_task_id', 'input_json', 'payload_schema_version'},
    'schema_migrations': {'version'},
    'image_task_receipts': {'job_id', 'route_index', 'provider', 'model', 'intent_digest', 'connection_digest', 'state', 'task_id', 'provider_status'},
    'job_attempts': {'id', 'job_id', 'stage', 'status'},
    'job_dispatches': {'id', 'job_id', 'status', 'topic', 'payload_json'},
    'assistant_runs': {'id', 'session_id', 'status', 'runtime_managed'},
    'assistant_session_leases': {'session_id', 'run_id', 'owner_token', 'expires_ms'},
    'assistant_actions': {'id', 'version', 'status', 'job_id'},
    'assistant_action_revisions': {'action_id', 'version', 'binding_digest', 'body_json'},
    'assistant_action_execution_grants': {'job_id', 'action_id', 'version', 'binding_digest', 'intent_digest', 'connections_json'},
    'assistant_action_binding_key': {'id', 'value'},
    'job_submission_keys': {'job_id'},
    'video_job_origins': {'job_id', 'provider', 'model', 'intent_digest', 'connection_digest', 'external_task_digest', 'submitted_at'},
    'video_tasks': {'task_id', 'status'},
}


class UpgradeInventorySchemaError(ValueError):
    """仅携带盘点合同内的缺失结构，不回显库中未知名称和业务内容。"""

    def __init__(self, missing_tables, missing_columns):
        super().__init__('upgrade_inventory_schema_unsupported')
        self.missing_tables = sorted(missing_tables)
        self.missing_columns = {table: sorted(columns) for table, columns in sorted(missing_columns.items())}


class _Findings:
    """完整计数与有界明细分离；跨领域合并后仍优先保留阻塞项。"""
    def __init__(self, limit):
        self.limit = limit
        self.counts = Counter()
        self.total = self.blockers = self.activity = 0
        self.blocked, self.other = [], []

    def detail(self, item):
        target = self.blocked if item['blocks_inventory_gate'] else self.other
        if len(target) < self.limit:
            target.append(item)

    def add(self, reason, blocked, *, active=False, **facts):
        self.counts[reason] += 1
        self.total += 1
        self.blockers += int(blocked)
        self.activity += int(active)
        self.detail({'reason': reason, 'blocks_inventory_gate': blocked, **facts})

    def video(self, report):
        self.counts.update({'video.' + k: v for k, v in report['reason_counts'].items()})
        self.total += report['findings_total']
        self.blockers += report['blocker_count']
        for row in report['details']:
            item = dict(row)
            item['reason'] = 'video.' + item['reason']
            item['blocks_inventory_gate'] = item.pop('blocks_task_gate')
            self.detail(item)


def _schema(conn, out):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing_tables = set(_TABLES) - tables
    missing_columns = {table: required - {r[1] for r in conn.execute('PRAGMA table_info(' + table + ')')}
                       for table, required in _TABLES.items() if table in tables}
    missing_columns = {table: columns for table, columns in missing_columns.items() if columns}
    if 'jobs' not in tables or missing_columns:
        raise UpgradeInventorySchemaError(missing_tables, missing_columns)
    for table in _TABLES:
        if table not in tables:
            out.add('schema_table_missing', True, table=table)
    if 'assistant_action_binding_key' in tables:
        valid = conn.execute("SELECT count(*) FROM assistant_action_binding_key WHERE id=1 AND length(value)=64 AND value NOT GLOB '*[^0-9a-f]*'").fetchone()[0]
        if valid != 1:
            out.add('binding_material_unavailable', True)
    if 'schema_migrations' in tables:
        known = set(BASE_SCHEMA_VERSIONS) | {version for version, _ in MIGRATIONS}
        for row in conn.execute('SELECT version FROM schema_migrations ORDER BY version'):
            if row[0] not in known:
                out.add('schema_migration_unknown', True, migration_digest=hashlib.sha256(str(row[0]).encode()).hexdigest())
        applied = {r[0] for r in conn.execute('SELECT version FROM schema_migrations') if r[0] in known}
        for version, _ in MIGRATIONS:
            # 审阅表用于附加诊断，旧库仍按原发布门槛评估。
            if version not in applied and version != LEGACY_JOB_REVIEWS_VERSION:
                out.add('schema_migration_missing', True, migration=version)
    return tables


def _image_reason(conn, job, tables, payload):
    records = image_task_receipts(job['id'], conn=conn) if 'image_task_receipts' in tables else []
    state = recovery_state(job)
    try:
        # 旧终态没有载荷可以保留；现存载荷损坏不能当成无执行证据。
        if job.get('input_json') is None and not records and job['status'] not in {'queued', 'running'}:
            if {'assistant_actions', 'assistant_action_execution_grants'} <= tables:
                if load_grant(conn, {**job, 'input_json': '{}'}) is not None:
                    return 'image_evidence_invalid', True
            return ('image_' + state, state in {'submission_unresolved', 'existing_task_unresolved', 'state_unavailable'})
        if not isinstance(payload, dict) or type(payload.get('schema_version')) is not int or payload['schema_version'] != 1:
            raise ValueError('invalid_payload')
        staged = payload.get('async_image')
        if staged is not None and (not isinstance(staged, dict) or type(staged.get('version')) is not int or staged['version'] != 1):
            raise ValueError('unknown_image_pipeline')
        if {'assistant_actions', 'assistant_action_execution_grants'} <= tables:
            load_grant(conn, job)
        else:
            return 'image_source_evidence_unavailable', True
        accepted = []
        for receipt in records:
            if (staged is None or receipt['state'] not in {'prepared', 'uncertain', 'accepted', 'rejected'}
                    or not _DIGEST.fullmatch(str(receipt['connection_digest']))
                    or receipt['intent_digest'] != job_intent_digest(job)):
                raise ValueError('invalid_receipt')
            index = receipt['route_index']
            routes = payload['route']['routes']
            if type(index) is not int or index < 0 or index >= len(routes) or receipt['provider'] != 'modelscope':
                raise ValueError('invalid_receipt_route')
            if (receipt['provider'], receipt['model']) != (routes[index]['provider'], routes[index]['model']):
                raise ValueError('invalid_receipt_route')
            if receipt['state'] == 'accepted':
                if not isinstance(receipt['task_id'], str) or not receipt['task_id']:
                    raise ValueError('invalid_receipt_identity')
                accepted.append(receipt)
            elif receipt['task_id'] is not None:
                raise ValueError('invalid_receipt_identity')
        if any(r['state'] in {'prepared', 'uncertain'} for r in records):
            return 'image_submission_unresolved', True
        if len(accepted) > 1:
            raise ValueError('ambiguous_receipt')
        if accepted:
            receipt = accepted[0]
            if (job['external_task_id'], job['provider'], job['model']) != (receipt['task_id'], receipt['provider'], receipt['model']):
                raise ValueError('changed_external_identity')
            if receipt['provider_status'] not in {None, 'PENDING', 'RUNNING', 'SUCCEED', 'FAILED'}:
                raise ValueError('unknown_provider_status')
            if job['status'] == 'succeeded' and receipt['provider_status'] == 'SUCCEED':
                return 'image_terminal_record', False
            if state == 'provider_failed' and receipt['provider_status'] == 'FAILED':
                return 'image_provider_failed', False
            if job['status'] not in {'queued', 'running'}:
                return 'image_existing_task_unresolved', True
            return 'image_original_task_in_progress', False
        if job['external_task_id'] or job['stage'] in {'image_poll', 'image_import'}:
            return 'image_origin_unverified', True
        if state in {'submission_unresolved', 'existing_task_unresolved', 'state_unavailable'}:
            return 'image_' + state, True
        return 'image_' + state, False
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RuntimeError, MediaExecutionRejected):
        return 'image_evidence_invalid', True


def _jobs(conn, tables, out):
    totals = Counter(image=0, video=0, unknown=0)
    for row in conn.execute('SELECT * FROM jobs ORDER BY id'):
        job = dict(row)
        kind = job['kind'] if job['kind'] in {'image', 'video'} else 'unknown'
        totals[kind] += 1
        facts = {'job_id': job_reference(job['id']), 'kind': kind}
        if job['status'] not in VALID_JOB_STATUSES or kind == 'unknown':
            out.add('job_state_unknown', True, **facts)
        if job['stage'] not in VALID_JOB_STAGES or job['payload_schema_version'] != 1:
            out.add('job_contract_unknown', True, **facts)
        payload = None
        if job.get('input_json') is not None:
            try:
                payload = json.loads(job['input_json'])
                if not isinstance(payload, dict) or ('schema_version' in payload and (type(payload['schema_version']) is not int or payload['schema_version'] != 1)):
                    raise ValueError('unknown_payload')
            except (TypeError, ValueError):
                out.add('job_payload_invalid', True, **facts)
        if job['status'] in {'queued', 'running'}:
            out.add('job_active', True, active=True, **facts)
            if job['stage'] not in JOB_STAGES_BY_KIND.get(kind, set()):
                out.add('job_stage_incompatible', True, **facts)
        if kind == 'image':
            reason, blocked = _image_reason(conn, job, tables, payload)
            if reason == 'image_submission_unresolved' and has_legacy_submission_rejection(conn, job):
                reason, blocked = 'image_recorded_submission_rejection', False
            out.add(reason, blocked, **facts)
    if 'image_task_receipts' in tables:
        for row in conn.execute('SELECT r.* FROM image_task_receipts r LEFT JOIN jobs j ON j.id=r.job_id WHERE j.id IS NULL ORDER BY r.job_id,r.route_index'):
            valid = (row['provider'] == 'modelscope' and type(row['route_index']) is int and row['route_index'] >= 0
                     and _DIGEST.fullmatch(str(row['connection_digest'])) and _DIGEST.fullmatch(str(row['intent_digest'])))
            resolved = valid and (row['state'] == 'rejected' and row['task_id'] is None or
                                 row['state'] == 'accepted' and row['task_id'] and row['provider_status'] in {'SUCCEED', 'FAILED'})
            out.add('image_orphaned_receipt_retained' if resolved else 'image_orphaned_task_unresolved', not bool(resolved), job_id=job_reference(row['job_id']))
    return dict(totals)


def _queue(conn, tables, out):
    totals = Counter(attempts=0, dispatches=0)
    if 'job_attempts' in tables:
        for row in conn.execute('SELECT id,job_id,stage,status FROM job_attempts ORDER BY id'):
            totals['attempts'] += 1
            if row['status'] == 'running':
                out.add('job_attempt_running', True, active=True, job_id=job_reference(row['job_id']))
            elif row['status'] not in {'succeeded', 'failed', 'canceled'} or row['stage'] not in EXECUTABLE_STAGES:
                out.add('job_attempt_contract_unknown', True, job_id=job_reference(row['job_id']))
    if 'job_dispatches' in tables:
        for row in conn.execute('SELECT d.*,j.status AS job_status,j.kind AS job_kind FROM job_dispatches d LEFT JOIN jobs j ON j.id=d.job_id ORDER BY d.id'):
            totals['dispatches'] += 1
            facts = {'dispatch_id': job_reference(row['id']), 'job_id': job_reference(row['job_id'])}
            if row['status'] not in {'pending', 'publishing', 'published', 'failed', 'canceled'}:
                out.add('outbox_state_unknown', True, **facts)
                continue
            if row['status'] not in {'pending', 'publishing', 'published'}:
                continue
            try:
                payload = json.loads(row['payload_json'])
                JobStageMessage(job_id=row['job_id'], stage=payload['stage'], attempt=payload.get('attempt', 1), dispatch_id=row['id'], trace_id=row['id'])
                if (row['topic'] != WORKER_TASK_NAME or type(payload.get('payload_schema_version')) is not int or payload['payload_schema_version'] != 1
                        or payload.get('job_id') != row['job_id'] or payload.get('job_kind') not in JOB_STAGES_BY_KIND
                        or payload['stage'] not in JOB_STAGES_BY_KIND[payload['job_kind']]
                        or row['job_kind'] is not None and payload['job_kind'] != row['job_kind']):
                    raise ValueError('unknown_dispatch_contract')
            except (ValueError, TypeError, KeyError, AttributeError):
                out.add('outbox_contract_unknown', True, **facts)
                continue
            if row['job_status'] is None:
                out.add('outbox_job_missing', True, **facts)
            elif row['status'] == 'publishing' or (row['status'] == 'pending' and row['job_status'] in {'queued', 'running'}):
                out.add('outbox_work_pending', True, active=True, **facts)
            else:
                # 已完成任务的迟到守护投递保留为事实；不把它当成又一次生成。
                out.add('outbox_delivery_retained', False, **facts)
    return {name: totals[name] if table in tables else None for name, table in
            (('attempts', 'job_attempts'), ('dispatches', 'job_dispatches'))}


def _assistant(conn, tables, out):
    totals = Counter(runs=0, lease_slots=0, proposals=0)
    if 'assistant_runs' in tables:
        for row in conn.execute('SELECT id,status,runtime_managed FROM assistant_runs ORDER BY id'):
            totals['runs'] += 1
            if row['status'] in ACTIVE_RUN_STATUSES:
                out.add('assistant_run_active', True, active=True, run_id=job_reference(row['id']))
            if row['status'] not in ACTIVE_RUN_STATUSES + TERMINAL_RUN_STATUSES or row['runtime_managed'] not in {0, 1}:
                out.add('assistant_run_state_unknown', True, run_id=job_reference(row['id']))
    if 'assistant_session_leases' in tables:
        for row in conn.execute('SELECT run_id,owner_token,expires_ms FROM assistant_session_leases ORDER BY session_id'):
            totals['lease_slots'] += 1
            if any(row[name] is not None for name in ('run_id', 'owner_token', 'expires_ms')):
                # 过期不是已停止的证明，盘点不能替运行服务回收租约。
                out.add('assistant_lease_held', True, active=True, run_id=job_reference(row['run_id']))
    if 'assistant_actions' in tables:
        for row in conn.execute('SELECT id,version,status,job_id FROM assistant_actions ORDER BY id'):
            totals['proposals'] += 1
            if row['status'] not in {'draft', 'submitted', 'revoked'}:
                out.add('assistant_proposal_state_unknown', True, action_id=job_reference(row['id']))
            elif row['status'] == 'draft':
                valid = False
                if 'assistant_action_revisions' in tables:
                    revision = conn.execute('SELECT binding_digest,body_json FROM assistant_action_revisions WHERE action_id=? AND version=?', (row['id'], row['version'])).fetchone()
                    try:
                        body = json.loads(revision['body_json'])
                        valid = isinstance(body, dict) and submission_intent_hash(body) == revision['binding_digest'] and row['job_id'] is None
                    except (TypeError, ValueError):
                        pass
                out.add('assistant_draft_retained' if valid else 'assistant_draft_invalid', not valid, action_id=job_reference(row['id']))
    if 'assistant_action_execution_grants' in tables:
        for row in conn.execute('SELECT g.job_id FROM assistant_action_execution_grants g LEFT JOIN jobs j ON j.id=g.job_id WHERE j.id IS NULL ORDER BY g.job_id'):
            out.add('assistant_grant_job_missing', True, job_id=job_reference(row[0]))
    return {name: totals[name] if table in tables else None for name, table in
            (('runs', 'assistant_runs'), ('lease_slots', 'assistant_session_leases'), ('proposals', 'assistant_actions'))}


def inventory_application_upgrade(database, *, detail_limit=1000, include_legacy_reviews=False):
    """只读取显式主库的一次快照；不读取检查点正文、环境凭据或其他数据库。"""
    if type(detail_limit) is not int or not 0 <= detail_limit <= 10000:
        raise ValueError('upgrade_inventory_detail_limit_invalid')
    out = _Findings(detail_limit)
    with readonly_snapshot(database) as conn:
        tables = _schema(conn, out)
        video = scan_video_upgrade(conn, detail_limit)
        out.video(video)
        jobs = _jobs(conn, tables, out)
        queue = _queue(conn, tables, out)
        assistant = _assistant(conn, tables, out)
        review_projection = {'legacy_reviews': scan_legacy_job_reviews(conn, detail_limit)} if include_legacy_reviews else {}
    details = (out.blocked + out.other)[:detail_limit]
    return {'schema_version': 1, 'scope': 'local_application_upgrade_evidence', 'scan_complete': True,
            'inventory_gate': 'blocked' if out.blockers else 'no_recorded_blockers', 'deployment_ready': False,
            'drain_observation': 'recorded_work_present' if out.activity else 'unverified' if out.blockers else 'no_recorded_work',
            'job_counts': jobs, 'queue_counts': queue, 'assistant_counts': assistant,
            'video_task_gate': video['task_gate'], 'missing_evidence_tables': sorted(set(_TABLES) - tables),
            'blocker_count': out.blockers, 'reason_counts': dict(sorted(out.counts.items())),
            'findings_total': out.total, 'details_omitted': out.total - len(details), 'details': details,
            **review_projection,
            'not_verified': ['ingress_and_processes_stopped', 'broker_drained', 'checkpoint_and_media_snapshot_consistency',
                             'original_connection_available', 'provider_task_outcome', 'backup_restore',
                             'configured_path_mapping', 'target_package_and_schema_compatibility', 'legacy_clients']}
