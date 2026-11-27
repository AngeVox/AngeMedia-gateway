"""队列动作的执行前门槛：凭据独立于会话正文，调用只读取本次固定的材料。"""
from __future__ import annotations

from contextlib import closing, contextmanager, ExitStack
import hmac
import json

from .. import config as C
from ..db.connection import db_connect
from ..db.readonly_scope import readonly_database
from ..job_submission import submission_intent_hash
from ..media_execution_scope import MediaExecutionRejected, MediaExecutionSnapshot, media_execution_scope
from ..providers.runtime_config import resolve_live_provider_runtime_config
from ..providers.transport_config import resolve_live_provider_transport
from ..reference_images import freeze_image_references as _freeze_references
from ..repositories.assistant_action_execution import load_grant
from ..repositories.jobs import get_job
from ..pollinations_video import is_pollinations_video_job
from ..repositories.pollinations_video_receipts import video_receipt
from ..repositories.reference_relay_config import get_live_reference_relay_config
from ..repositories.settings import get_custom_provider
from .assistant_action_connections import connection_binding
from .assistant_action_policy import action_policy
from .assistant_action_preparation import ActionPreparer


def _receipt(path, job_id, phase):
    with closing(db_connect(path=path)) as conn:
        job = get_job(job_id, conn=conn)
        if job is None:
            raise MediaExecutionRejected('action_execution_job_missing')
        grant = load_grant(conn, job)
        if grant is not None:
            expected = 'image_generate' if job['kind'] == 'image' else 'video_submit'
            if phase == 'followup':
                stages = {'video': {'video_poll', 'asset_import'}, 'image': {'image_poll', 'image_import'}}
                cached_video = False
                if is_pollinations_video_job(job) and job['stage'] == 'asset_import' and not job.get('external_task_id'):
                    receipt = video_receipt(conn, job_id)
                    cached_video = bool(receipt and receipt['state'] == 'cached')
                if ((not job.get('external_task_id') and not cached_video)
                    or (job['kind'] == 'image' and json.loads(job['input_json']).get('async_image', {}).get('version') != 1)):
                    raise MediaExecutionRejected('action_execution_phase_invalid')
                expected = job['stage'] if job['stage'] in stages.get(job['kind'],set()) else None
            if job['status'] != 'running' or job['stage'] != expected:
                raise MediaExecutionRejected('action_execution_job_changed')
        return job, grant


def _check_connections(bindings):
    for item in bindings:
        if not hmac.compare_digest(connection_binding(item['provider']), item['connection']):
            raise MediaExecutionRejected('action_execution_connection_changed')


def _capture_connections(bindings):
    runtimes, custom, relay, transports = {}, {}, None, {}
    for item in bindings:
        provider = item['provider']
        transport_id = provider.removeprefix('custom:')
        if provider != 'reference_relay':
            transports[transport_id] = resolve_live_provider_transport(transport_id)
        if provider == 'reference_relay':
            relay = get_live_reference_relay_config()
            observed = connection_binding(provider, relay=relay)
        elif provider.startswith('custom:'):
            provider_id = provider.split(':', 1)[1]
            custom[provider_id] = get_custom_provider(provider_id, include_secret=True)
            observed = connection_binding(provider, custom=custom[provider_id], transport=transports[transport_id])
        else:
            runtimes[provider] = resolve_live_provider_runtime_config(provider)
            observed = connection_binding(provider, runtime=runtimes[provider], transport=transports[transport_id])
        if not hmac.compare_digest(observed, item['connection']):
            raise MediaExecutionRejected('action_execution_connection_changed')
    return runtimes, custom, relay, transports


def _prepare_snapshot(path, job, phase, resources, target=None):
    try:
        with readonly_database(path):
            current, grant = _receipt(path, job['id'], phase)
            if grant is None:
                snapshot = None
            else:
                if phase not in {'submit', 'followup'}:
                    raise MediaExecutionRejected('action_execution_phase_invalid')
                connections = json.loads(grant['connections_json'])
                if phase == 'followup':
                    # 中转仅用于首次上传，受理后的取回不再依赖中转配置或参考图。
                    connections = [item for item in connections if item['provider'] != 'reference_relay']
                    if job['kind']=='image':
                        connections=[item for item in connections if target==(item['provider'],item['model'])]
                        if len(connections)!=1:
                            raise MediaExecutionRejected('action_execution_target_changed')
                body = None
                if phase == 'submit':
                    payload = json.loads(current['input_json'])
                    candidate = ActionPreparer(path).prepare(current['kind'], payload['request'])
                    body = candidate.body()
                    if not body['review']['allowed'] or not hmac.compare_digest(candidate.digest, grant['binding_digest']):
                        raise MediaExecutionRejected('action_execution_conditions_changed')
                references = _freeze_references(body['review']['references'], resources) if body else {}
                runtimes, custom, relay, transports = _capture_connections(connections)

                def validate():
                    # 每条回退路由及每次后续轮询都有最后门槛；已开始的请求始终用已批准的快照。
                    with readonly_database(path):
                        _, latest = _receipt(path, job['id'], phase)
                        if latest is None or latest != grant:
                            raise MediaExecutionRejected('action_execution_receipt_changed')
                        _check_connections(connections)
                        if body is not None:
                            policy, bindings = action_policy(body['admission'], body['review']['references'])
                            observed = {**body, 'review': {**body['review'], **policy}, 'configuration_bindings': bindings}
                            if not policy['allowed'] or submission_intent_hash(observed) != grant['binding_digest']:
                                raise MediaExecutionRejected('action_execution_conditions_changed')

                validate()
                targets = [(item['provider'], item['model']) for item in connections if item['provider'] != 'reference_relay']
                snapshot = MediaExecutionSnapshot(targets=targets, runtimes=runtimes, custom=custom,
                    references=references, relay=relay, transports=transports, validate=validate, operation=phase)
    except MediaExecutionRejected:
        raise
    except Exception:
        # 准备/配置/文件异常可能含路径或密钥，执行诊断只保留固定分类。
        raise MediaExecutionRejected('action_execution_validation_failed') from None
    return snapshot


@contextmanager
def action_job_execution(job, *, phase='submit', target=None):
    """先验证，再建立上下文；不持有SQL写锁调用供应商，退出路径统一关闭引用材料。"""
    with ExitStack() as resources:
        snapshot = _prepare_snapshot(C.DB_FILE.resolve(), job, phase, resources, target)
        if snapshot is None:
            yield
        else:
            with media_execution_scope(snapshot):
                yield
