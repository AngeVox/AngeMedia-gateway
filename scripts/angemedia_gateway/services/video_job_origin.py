"""视频真实执行的连接来源；队列与即时入口共用快照，不认领旧历史。"""
from contextlib import closing, contextmanager, ExitStack
import hmac
import json

from .. import config as C
from ..db.connection import db_connect, db_transaction
from ..db.readonly_scope import readonly_database
from ..media_execution_scope import MediaExecutionSnapshot, current_execution_scope, media_execution_scope
from ..providers.runtime_config import resolve_live_provider_runtime_config
from ..providers.transport_config import resolve_live_provider_transport
from ..reference_images import freeze_image_references
from ..providers.reference_delivery import prepare_builtin_reference, RELAY_REQUIRED
from ..repositories.assistant_action_execution import job_intent_digest
from ..repositories.jobs import get_job
from ..repositories.reference_relay_config import get_live_reference_relay_config
from ..repositories.video_job_origins import (VideoOriginRejected, external_task_digest,
    get_video_job_origin, record_video_job_origin, is_direct_video_job, direct_request_digest)
from .assistant_action_execution import action_job_execution
from .assistant_action_references import inspect_references
from .assistant_action_connections import needs_reference_relay
from .media_connection_binding import connection_binding


@contextmanager
def video_job_execution(job, *, phase='submit', request=None):
    path = C.DB_FILE.resolve()
    direct = is_direct_video_job(job)
    payload = json.loads(job['input_json'])
    if direct and phase == 'submit' and (request is None or
            direct_request_digest(request) != payload.get('request_digest')):
        raise VideoOriginRejected('video_origin_job_changed')
    with action_job_execution(job, phase=phase):
        inherited = current_execution_scope()
        with readonly_database(path), closing(db_connect()) as conn:
            origin = get_video_job_origin(job['id'], conn=conn)
        if phase == 'followup' and origin is None:
            if inherited is None:
                raise VideoOriginRejected('video_origin_unverified')
            # 旧助手只沿用已验证的原收据，不将旧任务回填成新来源。
            yield
            return
        runtime = inherited.runtime('agnes_video') if inherited else resolve_live_provider_runtime_config('agnes_video')
        transport = inherited.transport('agnes_video') if inherited else resolve_live_provider_transport('agnes_video')
        connection = connection_binding('agnes_video', runtime=runtime, transport=transport)
        request_body = request.model_dump(mode='json') if direct and request is not None else payload.get('request', {})
        relay_needed = False
        if direct and phase == 'submit':
            # 内联图片仅在本次调用的不可变请求中使用；观察文件引用时不把它当远程URL。
            request_body = dict(request_body)
            for field in ('image', 'images', 'first_frame', 'last_frame'):
                value = request_body.get(field)
                values = value if isinstance(value, list) else [value] if value else []
                relay_needed |= any(prepare_builtin_reference('agnes_video', item, model=job['model']).kind == RELAY_REQUIRED
                                    for item in values)
                retained = [item for item in values if not item.startswith('data:')]
                request_body[field] = retained if isinstance(value, list) else retained[0] if retained else None
        references = inspect_references(request_body)[0] if phase == 'submit' and not inherited else []
        relay = None
        relay_connection = None
        if relay_needed or needs_reference_relay('agnes_video', job['model'], references):
            relay = get_live_reference_relay_config()
            if relay.get('mode') != 'external_http' or not relay.get('upload_url'):
                raise VideoOriginRejected('video_origin_reference_relay_unavailable')
            relay_connection = connection_binding('reference_relay', relay=relay)
        if phase == 'submit':
            with db_transaction(immediate=True, path=path) as conn:
                current = get_job(job['id'], conn=conn)
                if (current is None or current['version'] != job['version'] or current['status'] != 'running'
                        or current['kind'] != 'video' or current['provider'] != 'agnes_video'
                        or current['stage'] != ('admitted' if direct else 'video_submit') or current.get('external_task_id')
                        or job_intent_digest(current) != job_intent_digest(job)):
                    raise VideoOriginRejected('video_origin_job_changed')
                origin = record_video_job_origin(conn, current, connection)
        if not hmac.compare_digest(connection, origin['connection_digest']):
            raise VideoOriginRejected('video_origin_connection_changed')

        def validate():
            with readonly_database(path), closing(db_connect()) as conn:
                current = get_job(job['id'], conn=conn)
                latest = get_video_job_origin(job['id'], conn=conn)
                if (current is None or latest != origin or current['version'] != job['version']
                        or current['status'] != 'running' or current['stage'] != job['stage']
                        or job_intent_digest(current) != origin['intent_digest']):
                    raise VideoOriginRejected('video_origin_job_changed')
                if phase == 'followup' and (not current.get('external_task_id')
                        or external_task_digest(current['external_task_id']) != origin['external_task_digest']):
                    raise VideoOriginRejected('video_origin_task_changed')
                if phase == 'submit' and (current.get('external_task_id') or origin['external_task_digest']):
                    raise VideoOriginRejected('video_origin_task_changed')
                if not hmac.compare_digest(connection_binding('agnes_video'), origin['connection_digest']):
                    raise VideoOriginRejected('video_origin_connection_changed')
                if relay_connection is not None and not hmac.compare_digest(connection_binding('reference_relay'), relay_connection):
                    raise VideoOriginRejected('video_origin_connection_changed')

        validate()
        if inherited:
            with inherited.validation_guard(validate):
                yield
        else:
            with ExitStack() as resources:
                # 普通图生视频也固定引用；不能因新增运行时快照而丢失原有图片输入。
                frozen = freeze_image_references(references, resources)
                snapshot = MediaExecutionSnapshot(targets=[('agnes_video', job['model'])], runtimes={'agnes_video': runtime},
                    custom={}, references=frozen, relay=relay, transports={'agnes_video': transport}, validate=validate, operation=phase)
                with media_execution_scope(snapshot):
                    yield
