"""异步图片阶段编排；收据和下一次投递同事务，恢复从不再次POST。"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import asyncio
import json

from .. import config as C
from ..db.connection import db_connect, db_transaction
from ..helpers import now_iso
from ..media import openai_image_response
from ..media_execution_scope import MediaExecutionRejected, require_execution_target
from ..providers.errors import BackendUnavailable, ProviderError
from ..queue.contracts import QueueDispatchEnvelope
from ..queue.settings import WORKER_TASK_NAME
from ..repositories.assistant_action_execution import job_intent_digest
from ..repositories.image_task_receipts import accepted_image_task, finish_image_submission, image_task_receipts, record_image_observation
from ..repositories.job_attempts import create_job_attempt, finish_job_attempt, get_job_attempt, get_running_stage_attempt
from ..repositories.job_dispatches import create_job_dispatch
from ..repositories.job_events import append_job_event
from ..repositories.jobs import claim_job_attempt, get_job, transition_job_in_connection
from ..schemas import ImageRequest
from .assistant_action_execution import action_job_execution
from .image_execution import ImageTaskSubmission
from .image_job_admission import parse_image_job_payload
from .image_task_execution import image_task_execution
from .job_lifecycle import StaleJobVersion


def staged_image_job(job):
    try:
        return job.get('kind')=='image' and json.loads(job['input_json']).get('async_image',{}).get('version')==1
    except (TypeError,ValueError,AttributeError):
        return False


class ImageAsyncWorker:
    def __init__(self, executor, success, failure, *, worker_kind='celery', now=None):
        self.executor,self.success,self.failure=executor,success,failure
        self.worker_kind=worker_kind
        self.now=now or (lambda:datetime.now(timezone.utc))

    @staticmethod
    def _result(message,status):
        return {'status':status,'job_id':message.job_id,'stage':message.stage,'attempt':message.attempt,'dispatch_id':message.dispatch_id}

    def _dispatch(self,conn,message,stage,available_at,*,attempt=None):
        envelope=QueueDispatchEnvelope(job_id=message.job_id,job_kind='image',stage=stage,payload_schema_version=1,
            attempt=message.attempt+1 if attempt is None else attempt)
        return create_job_dispatch(job_id=message.job_id,topic=WORKER_TASK_NAME,payload=envelope.as_dict(),available_at=available_at,conn=conn)

    def _claim(self,message,job):
        if not staged_image_job(job) or message.stage not in {'image_generate','image_poll','image_import'}:
            raise ValueError('异步图片任务阶段无效')
        with db_transaction(immediate=True) as conn:
            current=get_job(job['id'],conn=conn)
            if current is None or current['version']!=job['version'] or current['stage']!=message.stage or current['status'] not in {'queued','running'}:
                return None,'stale'
            existing=get_job_attempt(job['id'],message.attempt,conn=conn)
            if existing and (existing['status']!='running' or existing['stage']!=message.stage):
                return None,'duplicate'
            if existing and message.stage=='image_generate':
                return current,'resume'
            if not existing and get_running_stage_attempt(job['id'],message.stage,conn=conn):
                return None,'inflight'
            claimed=claim_job_attempt(conn,job_id=job['id'],expected_version=job['version'],stage=message.stage,
                attempt_count=message.attempt,worker_kind=self.worker_kind,provider=job['provider'],model=job['model'],
                started_at=now_iso(),allow_running=True)
            if claimed is None:return None,'stale'
            if not existing:
                detail={'dispatch_id':message.dispatch_id}
                if message.stage=='image_generate':
                    payload=json.loads(job['input_json'])
                    # 守护投递与首次领取同事务；进程在任意提交窗口退出都有后续收敛入口。
                    seconds=max(60,float(C.HTTP_TIMEOUT)*4*len(payload['route']['routes'])+float(payload['async_image']['maximum']))
                    detail['submit_deadline']=(self.now()+timedelta(seconds=seconds)).isoformat()
                    self._dispatch(conn,message,'image_generate',detail['submit_deadline'],attempt=message.attempt)
                create_job_attempt(job_id=job['id'],attempt_number=message.attempt,stage=message.stage,
                    worker_kind=self.worker_kind,status='running',started_at=now_iso(),detail=detail,conn=conn)
            append_job_event(job['id'],'worker_attempt_started',{'attempt':message.attempt},stage=message.stage,conn=conn)
            return claimed,None

    def _fail(self,message,job,code,category='upstream',error=None):
        self.failure(message,error or BackendUnavailable(code),code,expected_version=job['version'],
            diagnostic={'error_category':category,'human_hint':'','retryable':False,'gateway_stage':message.stage})
        return self._result(message,'failed')

    def _policy(self,job):
        return json.loads(job['input_json'])['async_image']

    def _expired(self,message,job):
        start=datetime.fromisoformat(job['started_at'].replace('Z','+00:00'))
        if start.tzinfo is None:start=start.replace(tzinfo=timezone.utc)
        return message.attempt>=job['max_attempts'] or (self.now()-start).total_seconds()>=float(self._policy(job)['maximum'])

    def _schedule(self,message,job,receipt,stage,*,task_id=None,status=None,result_url=None,delay=True,error_code=''):
        interval=float(self._policy(job)['interval'])
        wait=min(60,interval*2**min(max(message.attempt-2,0),5)) if delay else 0
        available=(self.now()+timedelta(seconds=wait)).isoformat()
        with db_transaction(immediate=True) as conn:
            if task_id is not None:finish_image_submission(conn,receipt,task_id=task_id)
            current=get_job(job['id'],conn=conn)
            if current is None or job_intent_digest(current)!=receipt['intent_digest']:
                raise MediaExecutionRejected('image_task_job_changed')
            if task_id is not None and current['status']=='failed':
                # 守护已结束本地等待时，只保留迟到受理事实，不复活终态或另发生成。
                transition_job_in_connection(conn,job['id'],expected_version=current['version'],status='failed',
                    provider=receipt['provider'],model=receipt['model'],external_task_id=task_id,provider_status='submitted')
                append_job_event(job['id'],'image_task_late_receipt',{},stage='finalize',conn=conn)
                return 'late_receipt'
            if current['version']!=job['version']:raise StaleJobVersion('异步图片执行版本已变化')
            if status is not None:record_image_observation(conn,receipt,status,result_url)
            transition_job_in_connection(conn,job['id'],expected_version=job['version'],status='running',stage=stage,
                provider=receipt['provider'],model=receipt['model'],external_task_id=task_id or receipt['task_id'],
                provider_status=status or current.get('provider_status') or 'submitted',
                output_json=json.dumps({'provider':receipt['provider'],'model':receipt['model'],
                    'provider_status':status or current.get('provider_status') or 'submitted','has_result':bool(result_url or receipt.get('result_url'))}),
                error_code=error_code,error_message='',error_category=('provider_identity' if error_code in
                    {'image_task_connection_changed','action_execution_connection_changed'} else 'provider_response') if error_code else '',
                retryable=1 if error_code else 0,next_retry_at=available)
            finish_job_attempt(job_id=job['id'],attempt_number=message.attempt,status='succeeded',completed_at=now_iso(),
                detail={'next_stage':stage},conn=conn)
            self._dispatch(conn,message,stage,available)
            return 'scheduled'

    def _accepted_recovery(self,message,job,receipt,task_id):
        """下一阶段投递写入失败时，仍尽力保存编号；原守护投递可恢复调度。"""
        with db_transaction(immediate=True) as conn:
            finish_image_submission(conn,receipt,task_id=task_id)
            current=get_job(job['id'],conn=conn)
            if current and job_intent_digest(current)==receipt['intent_digest']:
                transition_job_in_connection(conn,job['id'],expected_version=current['version'],status=current['status'],
                    provider=receipt['provider'],model=receipt['model'],external_task_id=task_id,provider_status='submitted',
                    error_code='image_task_dispatch_pending',retryable=False)
        # 编号先落盘；再尽力安排立即恢复。即使投递表持续失败，原守护投递也仍保留。
        try:
            with db_transaction(immediate=True) as conn:
                self._dispatch(conn,message,'image_generate',self.now().isoformat(),attempt=message.attempt)
        except Exception:
            pass

    def handle_generate(self,message,job):
        claimed,state=self._claim(message,job)
        if claimed is None:return self._result(message,state)
        try:
            if state=='resume':
                receipt=accepted_image_task(job['id'])
                if receipt:return self._result(message,self._schedule(message,claimed,receipt,'image_poll'))
                attempt=get_job_attempt(job['id'],message.attempt)
                deadline=json.loads(attempt['detail_json'])['submit_deadline']
                if self.now()<datetime.fromisoformat(deadline):return self._result(message,'inflight')
                result=self._fail(message,claimed,'image_submit_ambiguous','ambiguous_submit')
                with db_transaction(immediate=True) as conn:
                    for item in image_task_receipts(job['id'],conn=conn):
                        if item['state']=='prepared':finish_image_submission(conn,item)
                return result
            try:request,plan=parse_image_job_payload(json.loads(job['input_json']))
            except Exception as exc:return self._fail(message,claimed,'invalid_image_job_payload','validation',exc)

            async def submit(provider,req,target,index):
                with image_task_execution(claimed,route_index=index) as receipt:
                    require_execution_target('modelscope',target.model)
                    try:task_id=await provider.submit(req,target)
                    except ProviderError as exc:
                        with db_transaction(immediate=True) as conn:finish_image_submission(conn,receipt,rejected=not exc.request_committed)
                        raise
                    except MediaExecutionRejected:
                        with db_transaction(immediate=True) as conn:finish_image_submission(conn,receipt,rejected=True)
                        raise
                    except Exception as exc:
                        with db_transaction(immediate=True) as conn:finish_image_submission(conn,receipt)
                        error=BackendUnavailable('ModelScope submission outcome is unknown',error_category='ambiguous_submit')
                        error.request_committed=True
                        raise error from exc
                try:delivery=self._schedule(message,claimed,receipt,'image_poll',task_id=task_id)
                except Exception as exc:
                    self._accepted_recovery(message,claimed,receipt,task_id)
                    error=BackendUnavailable('ModelScope accepted task; dispatch persistence pending',error_category='queue_persistence')
                    error.request_committed=True
                    raise error from exc
                return ImageTaskSubmission(task_id,target.model,index,delivery)

            try:
                with action_job_execution(claimed):result=asyncio.run(self.executor.execute_staged(request,plan,submit))
                if isinstance(result,ImageTaskSubmission):return self._result(message,result.dispatch_status)
                self.success(message,request,result,expected_version=claimed['version'])
                return self._result(message,'succeeded')
            except Exception as exc:
                if accepted_image_task(job['id']):raise
                if isinstance(exc,MediaExecutionRejected):return self._fail(message,claimed,exc.code,'action_authorization',exc)
                rows=image_task_receipts(job['id'])
                uncertain=any(item['state'] in {'prepared','uncertain'} for item in rows) or any(provider!='modelscope' for provider,_ in plan.routes)
                return self._fail(message,claimed,'image_submit_ambiguous' if uncertain else 'image_submit_rejected',
                    'ambiguous_submit' if uncertain else 'upstream',exc)
        except StaleJobVersion:return self._result(message,'stale')

    def _retry(self,message,job,receipt,code,stage):
        if self._expired(message,job):return self._fail(message,job,'image_poll_timeout','local_timeout')
        return self._result(message,self._schedule(message,job,receipt,stage,error_code=code))

    def handle_poll(self,message,job):
        claimed,state=self._claim(message,job)
        if claimed is None:return self._result(message,state)
        try:
            receipt=accepted_image_task(job['id'])
            if not receipt:return self._fail(message,claimed,'image_task_origin_unverified','provider_identity')
            if self._expired(message,claimed):return self._fail(message,claimed,'image_poll_timeout','local_timeout')
            try:
                with action_job_execution(claimed,phase='followup',target=(receipt['provider'],receipt['model'])),image_task_execution(claimed):
                    require_execution_target(receipt['provider'],receipt['model'],operation='followup')
                    if not self.executor.provider_enabled_func(receipt['provider']):
                        raise MediaExecutionRejected('image_task_connection_changed')
                    task=asyncio.run(self.executor.providers[receipt['provider']].poll(receipt['task_id']))
            except MediaExecutionRejected as exc:
                if exc.code in {'image_task_connection_changed','action_execution_connection_changed'}:
                    return self._retry(message,claimed,receipt,exc.code,'image_poll')
                return self._fail(message,claimed,exc.code,'action_authorization',exc)
            except ProviderError as exc:
                if exc.retryable:return self._retry(message,claimed,receipt,'image_query_retry','image_poll')
                return self._fail(message,claimed,'image_query_failed',exc.error_category,exc)
            except Exception:
                return self._retry(message,claimed,receipt,'image_query_retry','image_poll')
            if task.task_id!=receipt['task_id']:return self._fail(message,claimed,'image_task_identity_changed','provider_identity')
            if task.status=='FAILED':
                with db_transaction(immediate=True) as conn:record_image_observation(conn,receipt,'FAILED')
                return self._fail(message,claimed,'image_generation_failed','task_failed')
            return self._result(message,self._schedule(message,claimed,receipt,
                'image_import' if task.status=='SUCCEED' else 'image_poll',status=task.status,result_url=task.image_url,delay=task.status!='SUCCEED'))
        except StaleJobVersion:return self._result(message,'stale')

    def handle_import(self,message,job):
        claimed,state=self._claim(message,job)
        if claimed is None:return self._result(message,state)
        try:
            receipt=accepted_image_task(job['id'])
            if not receipt or not receipt['result_url']:return self._fail(message,claimed,'image_task_result_missing','protocol')
            if self._expired(message,claimed):return self._fail(message,claimed,'image_import_timeout','local_timeout')
            try:
                request=ImageRequest.model_validate(json.loads(job['input_json'])['request'])
                with action_job_execution(claimed,phase='followup',target=(receipt['provider'],receipt['model'])),image_task_execution(claimed):
                    require_execution_target(receipt['provider'],receipt['model'],operation='followup')
                    result=asyncio.run(self.executor.complete_builtin(request,receipt['provider'],receipt['model'],
                        openai_image_response(url=receipt['result_url']),started_at=job['started_at']))
                self.success(message,request,result,expected_version=claimed['version'])
                return self._result(message,'succeeded')
            except MediaExecutionRejected as exc:
                if exc.code in {'image_task_connection_changed','action_execution_connection_changed'}:
                    return self._retry(message,claimed,receipt,exc.code,'image_import')
                return self._fail(message,claimed,exc.code,'action_authorization',exc)
            except StaleJobVersion:raise
            except Exception:return self._retry(message,claimed,receipt,'image_import_retry','image_import')
        except StaleJobVersion:return self._result(message,'stale')
