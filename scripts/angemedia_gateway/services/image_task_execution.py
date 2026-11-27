"""异步图片调用的来源和快照；受理后不再读取输入图片或中转配置。"""
from contextlib import closing, contextmanager, ExitStack
import hmac
import json

from .. import config as C
from ..db.connection import db_connect, db_transaction
from ..db.readonly_scope import readonly_database
from ..media_execution_scope import MediaExecutionRejected, MediaExecutionSnapshot, current_execution_scope, media_execution_scope
from ..providers.runtime_config import resolve_live_provider_runtime_config
from ..providers.transport_config import resolve_live_provider_transport
from ..reference_images import freeze_image_references
from ..repositories.assistant_action_execution import job_intent_digest
from ..repositories.image_task_receipts import accepted_image_task, image_task_receipts, record_image_submission
from ..repositories.jobs import get_job
from ..job_sanitizer import sanitize_error_text
from .assistant_action_references import inspect_references
from .media_connection_binding import connection_binding


@contextmanager
def image_task_execution(job, *, route_index=None):
    phase='submit' if route_index is not None else 'followup'
    path=C.DB_FILE.resolve(); inherited=current_execution_scope()
    payload=json.loads(job['input_json'])
    receipt=accepted_image_task(job['id']) if phase=='followup' else None
    if phase=='followup' and receipt is None:
        raise MediaExecutionRejected('image_task_origin_unverified')
    route=payload['route']['routes'][route_index] if phase=='submit' else receipt
    provider,model=route['provider'],route['model']
    runtime=inherited.runtime(provider) if inherited else resolve_live_provider_runtime_config(provider)
    transport=inherited.transport(provider) if inherited else resolve_live_provider_transport(provider)
    connection=connection_binding(provider,runtime=runtime,transport=transport)
    with ExitStack() as resources:
        references=freeze_image_references(inspect_references(payload['request'])[0],resources) if phase=='submit' and not inherited else {}
        if phase=='submit':
            with db_transaction(immediate=True,path=path) as conn:
                current=get_job(job['id'],conn=conn)
                if current is None or current['version']!=job['version'] or current['status']!='running' or current['stage']!='image_generate' or job_intent_digest(current)!=job_intent_digest(job):
                    raise MediaExecutionRejected('image_task_job_changed')
                receipt=record_image_submission(conn,current,route_index,connection)
        if not hmac.compare_digest(connection,receipt['connection_digest']):
            raise MediaExecutionRejected('image_task_connection_changed')

        def validate():
            with readonly_database(path),closing(db_connect()) as conn:
                current=get_job(job['id'],conn=conn)
                records=image_task_receipts(job['id'],conn=conn)
                latest=next((item for item in records if item['route_index']==receipt['route_index']),None)
                if current is None or current['version']!=job['version'] or current['status']!='running' or current['stage']!=job['stage'] or job_intent_digest(current)!=receipt['intent_digest']:
                    raise MediaExecutionRejected('image_task_job_changed')
                if latest!=receipt or not hmac.compare_digest(connection_binding(provider),receipt['connection_digest']):
                    raise MediaExecutionRejected('image_task_connection_changed')
                if phase=='followup' and (current['provider']!=provider or current['model']!=model or current.get('external_task_id')!=sanitize_error_text(receipt['task_id'],limit=1000)):
                    raise MediaExecutionRejected('image_task_identity_changed')
        validate()
        if inherited:
            with inherited.validation_guard(validate): yield receipt
        else:
            snapshot=MediaExecutionSnapshot(targets=[(provider,model)],runtimes={provider:runtime},custom={},references=references,
                relay=None,transports={provider:transport},validate=validate,operation=phase)
            with media_execution_scope(snapshot): yield receipt
