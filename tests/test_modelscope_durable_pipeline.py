"""真实适配器、任务事务与冷进程恢复；所有供应商响应均为受控HTTP。"""
import asyncio
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway import config as C
from angemedia_gateway.db.connection import db_connect, db_transaction
from angemedia_gateway.db.migrations import run_migrations
from angemedia_gateway.media_execution_scope import MediaExecutionRejected
from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.providers.errors import BackendUnavailable, ProviderError
from angemedia_gateway.providers.image.modelscope import ModelScopeProvider
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.image_task_receipts import accepted_image_task, image_task_receipts
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.repositories.provider_transport_config import update_provider_transport_config
from angemedia_gateway.schemas import ImageRequest
from angemedia_gateway.services.image_execution import ImageExecutionPlan, ImageExecutionService
from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService
from angemedia_gateway.services.image_job_worker import ImageJobWorker
from angemedia_gateway.services.job_control import regeneration_control
from angemedia_gateway.services.job_stage_registry import JobStageRegistry
from angemedia_gateway.services.worker_runtime import WorkerRuntime
from angemedia_gateway.presenters.assistant_jobs import assistant_job_observation
from test_assistant_action_processes import _service
from test_siliconflow_qwen_image_contract import PNG, REMOTE_URL

MODULE='angemedia_gateway.providers.image.modelscope'
KEY='fixture-modelscope-pipeline-123456789'


def _runtime(providers=None,**kwargs):
    worker=ImageJobWorker(executor=ImageExecutionService(providers=providers or {'modelscope':ModelScopeProvider()},**kwargs))
    runtime=WorkerRuntime(registry=JobStageRegistry({'image_generate':worker.handle,'image_poll':worker.handle_poll,'image_import':worker.handle_import}),runtime_refresher=lambda:None)
    return runtime,worker


def _cold_worker(path,job_id,mode,barrier,output):
    """独立解释器只共享SQLite和受控调用日志，进程退出不能靠父进程内存恢复。"""
    _service(path)
    calls=Path(path).parent/'calls.jsonl'
    def reply(request):
        kind='POST' if request.method=='POST' else 'POLL' if '/v1/tasks/' in request.url.path else 'DOWNLOAD'
        with calls.open('a',encoding='utf-8') as log:log.write(json.dumps({'kind':kind})+'\n')
        if mode=='crash_after_post' and kind=='POST':os._exit(71)
        if mode=='crash_poll' and kind=='POLL':os._exit(72)
        if mode=='crash_import' and kind=='DOWNLOAD':os._exit(73)
        if kind=='POST':return httpx.Response(200,json={'task_id':'cold-image-task'})
        if kind=='POLL':return httpx.Response(200,json={'task_status':'SUCCEED','output_images':[REMOTE_URL]})
        return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    @asynccontextmanager
    async def provider(**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:yield client
    def download():return httpx.AsyncClient(transport=httpx.MockTransport(reply))
    helper=ActionWorkerFixture();message=helper.message(job_id)
    runtime,worker=_runtime()
    if mode=='watchdog':worker.async_worker.now=lambda:datetime.now(timezone.utc)+timedelta(days=30)
    if barrier is not None:barrier.wait(30)
    with patch(MODULE+'.provider_client',new=provider),patch(MODULE+'.quota.try_acquire',new=AsyncMock(return_value=True)),patch('angemedia_gateway.media._remote_media_http_client',new=download):
        result=runtime.handle(message)
    if mode=='crash_after_receipt':os._exit(74)
    if output is not None:output.put(result)


class ModelScopeDurablePipelineTest(ActionWorkerFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        update_provider_runtime_config('modelscope',enabled=True,api_key=KEY,base_url_override='https://modelscope.example.test')
        self.context=multiprocessing.get_context('spawn'); self.children=[];self.queues=[]
        self.runtime,self.worker=_runtime()
        item=patch(MODULE+'.quota.try_acquire',new=AsyncMock(return_value=True));self.budget=item.start();self.addCleanup(item.stop)

    def tearDown(self):
        for child in self.children:
            child.join(2)
            if child.is_alive():child.terminate();child.join(5)
        for queue in self.queues:queue.close();queue.join_thread()
        super().tearDown()

    def action_job(self,**values):
        return self.job(self.propose(request={'prompt':'秋日山谷','model':'qwen',**values}))

    def clients(self,responses=None,callback=None):
        responses=list(responses or []); calls=[]
        def reply(request):
            calls.append({'method':request.method,'url':str(request.url),'authorization':request.headers.get('authorization'),
                'json':json.loads(request.content) if request.content else None})
            if callback:callback(request)
            if responses:
                result=responses.pop(0)
                if isinstance(result,BaseException):raise result
                if isinstance(result,httpx.Response):return result
                return httpx.Response(200,json=result)
            if request.method=='POST':return httpx.Response(200,json={'task_id':'image-task-1'})
            if '/v1/tasks/' in request.url.path:return httpx.Response(200,json={'task_status':'SUCCEED','output_images':[REMOTE_URL]})
            return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
        @asynccontextmanager
        async def provider(**kwargs):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:yield client
        def download():return httpx.AsyncClient(transport=httpx.MockTransport(reply))
        return provider,download,calls

    def patches(self,provider,download):
        from contextlib import ExitStack
        stack=ExitStack();stack.enter_context(patch(MODULE+'.provider_client',new=provider))
        stack.enter_context(patch('angemedia_gateway.media._remote_media_http_client',new=download));return stack

    def step(self,job):return self.runtime.handle(self.message(job['id']))

    def spawn(self,job,mode,barrier=None,output=None):
        child=self.context.Process(target=_cold_worker,args=(str(C.DB_FILE),job['id'],mode,barrier,output))
        self.children.append(child);child.start();child.join(40)
        self.assertFalse(child.is_alive());return child

    def call_counts(self):
        path=self.temp/'calls.jsonl'
        rows=[json.loads(line)['kind'] for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []
        return {kind:rows.count(kind) for kind in ('POST','POLL','DOWNLOAD')}

    def test_new_admission_marks_staged_jobs_without_changing_hash_or_legacy_payload_contract(self):
        request=ImageRequest(prompt='小屋',model='qwen')
        prepared=ImageJobAdmissionService().prepare(request).arguments()
        self.assertEqual(prepared['payload']['async_image']['version'],1);self.assertGreater(prepared['max_attempts'],3)
        self.assertEqual(prepared['stage'],'image_generate')
        ordinary=ImageJobAdmissionService().prepare(ImageRequest(prompt='小屋',model='kolors')).arguments()
        self.assertNotIn('async_image',ordinary['payload'])

    def test_real_confirmation_poll_import_and_terminal_replay_create_one_asset(self):
        job=self.action_job(); original=self.message(job['id']); provider,download,calls=self.clients()
        with self.patches(provider,download):
            self.assertEqual(self.step(job)['status'],'scheduled');self.assertEqual(get_job(job['id'])['stage'],'image_poll')
            self.assertEqual(self.step(job)['status'],'scheduled');self.assertEqual(get_job(job['id'])['stage'],'image_import')
            self.assertEqual(self.step(job)['status'],'succeeded');self.assertEqual(self.runtime.handle(original)['status'],'duplicate')
        self.assertEqual([r['method'] for r in calls],['POST','GET','GET']);self.assertIsNone(calls[-1]['authorization'])
        self.assertEqual(len(list_assets(job_id=job['id'])),1);self.assertEqual(accepted_image_task(job['id'])['task_id'],'image-task-1')
        self.assertIsNone(accepted_image_task(job['id'])['result_url'])
        self.observation={'fixture_only':True,'actual_generation_or_llm_calls':0,'production_database_read':False,
            'provider_posts':1,'provider_polls':1,'safe_downloads':1,'assets':1,'terminal_replay_http_calls':0,
            'original_confirmation_worker_localizer_assets':True,'source_receipt_and_transactional_outbox':True}

    def test_direct_job_uses_the_same_source_guard_without_assistant_grant(self):
        job=ImageJobAdmissionService().submit(ImageRequest(prompt='小屋',model='qwen')).job
        provider,download,calls=self.clients()
        with self.patches(provider,download):
            for _ in range(3):self.step(job)
        self.assertEqual(get_job(job['id'])['status'],'succeeded');self.assertEqual(len(calls),3)

    def test_mismatched_poll_identity_preserves_receipt_and_never_imports_or_resubmits(self):
        job=self.action_job()
        provider,download,calls=self.clients([
            {'task_id':'original-task'},
            {'task_id':'another-task','task_status':'SUCCEED','output_images':[REMOTE_URL]},
        ])
        with self.patches(provider,download):
            self.assertEqual(self.step(job)['status'],'scheduled')
            self.assertEqual(self.step(job)['status'],'failed')
            self.step(job)
        current=get_job(job['id'])
        receipt=accepted_image_task(job['id'])
        self.assertEqual(current['error_code'],'image_query_failed')
        self.assertEqual(current['error_category'],'protocol')
        self.assertEqual((receipt['task_id'],receipt['provider_status'],receipt['result_url']),('original-task',None,None))
        self.assertFalse(regeneration_control(current)['enabled'])
        self.assertEqual(list_assets(job_id=job['id']),[])
        self.assertEqual([r['method'] for r in calls],['POST','GET'])

    def test_submit_response_timeout_or_5xx_never_falls_through_to_next_model(self):
        for response in (httpx.Response(500),httpx.ReadTimeout('fixture'),{'task_id':None}):
            with self.subTest(response=type(response).__name__):
                req=ImageRequest(prompt='小屋');provider,download,calls=self.clients([response])
                plan=ImageExecutionPlan(mode='builtin',routes=(('modelscope','Qwen/Qwen-Image-2512'),('fixture_next','next')))
                next_call=AsyncMock(); executor=ImageExecutionService(providers={'modelscope':ModelScopeProvider(),'fixture_next':SimpleNamespace(generate=next_call)},provider_enabled_func=lambda _:True)
                with self.patches(provider,download),self.assertRaises(ProviderError) as caught:asyncio.run(executor.execute(req,plan))
                self.assertTrue(caught.exception.request_committed);next_call.assert_not_awaited();self.assertEqual(len(calls),1)

    def test_sync_accepted_poll_or_download_failure_cannot_trigger_another_generation(self):
        for index,responses in enumerate(([{'task_id':'task-1'},httpx.Response(429)], [{'task_id':'task-1'},{'task_status':'SUCCEED','output_images':[REMOTE_URL]},httpx.Response(500)])):
            provider,download,calls=self.clients(responses)
            next_call=AsyncMock();executor=ImageExecutionService(providers={'modelscope':ModelScopeProvider(),'next':SimpleNamespace(generate=next_call)},provider_enabled_func=lambda _:True)
            plan=ImageExecutionPlan(mode='builtin',routes=(('modelscope','Qwen/Qwen-Image-2512'),('next','next')))
            with patch.object(C,'POLL_INTERVAL',0),self.patches(provider,download):
                if index==0:
                    with self.assertRaises(ProviderError):asyncio.run(executor.execute(ImageRequest(prompt='小屋'),plan))
                else:
                    result=asyncio.run(executor.execute(ImageRequest(prompt='小屋'),plan))
                    self.assertFalse(result.result['data'][0]['localized'])
            next_call.assert_not_awaited();self.assertEqual(sum(r['method']=='POST' for r in calls),1)

    def test_explicit_rejection_allows_next_approved_model_and_keeps_both_receipts(self):
        for provider in ('siliconflow','agnes_image','bytedance','pollinations','openai_image'):update_provider_runtime_config(provider,enabled=False)
        job=self.job(self.propose(request={'prompt':'小屋'}))
        provider,download,calls=self.clients([httpx.Response(403),{'task_id':'second-model'},{'task_status':'SUCCEED','output_images':[REMOTE_URL]},httpx.Response(200,content=PNG,headers={'content-type':'image/png'})])
        with self.patches(provider,download):
            for _ in range(3):self.step(job)
        receipts=image_task_receipts(job['id']);self.assertEqual([r['state'] for r in receipts],['rejected','accepted'])
        self.assertEqual(get_job(job['id'])['model'],receipts[-1]['model']);self.assertEqual(get_job(job['id'])['status'],'succeeded')
        self.assertEqual(sum(r['method']=='POST' for r in calls),2)

    def test_uncertain_staged_submit_is_terminal_and_not_eligible_for_regeneration(self):
        job=self.action_job();provider,download,calls=self.clients([httpx.Response(500)])
        with self.patches(provider,download):self.assertEqual(self.step(job)['status'],'failed');self.step(job)
        current=get_job(job['id']);self.assertEqual(current['error_code'],'image_submit_ambiguous')
        self.assertFalse(regeneration_control(current)['enabled']);self.assertEqual(image_task_receipts(job['id'])[0]['state'],'uncertain');self.assertEqual(len(calls),1)

    def test_query_429_retries_only_get_with_original_task_id(self):
        job=self.action_job();provider,download,calls=self.clients([{'task_id':'task-1'},httpx.Response(429),{'task_status':'SUCCEED','output_images':[REMOTE_URL]},httpx.Response(200,content=PNG,headers={'content-type':'image/png'})])
        with self.patches(provider,download):
            for _ in range(4):self.step(job)
        self.assertEqual([r['method'] for r in calls],['POST','GET','GET','GET']);self.budget.assert_awaited_once()
        self.assertEqual(get_job(job['id'])['status'],'succeeded')

    def test_download_retry_imports_original_result_without_resubmit_or_requery(self):
        job=self.action_job();provider,download,calls=self.clients([{'task_id':'task-1'},{'task_status':'SUCCEED','output_images':[REMOTE_URL]},httpx.Response(500),httpx.Response(200,content=PNG,headers={'content-type':'image/png'})])
        with self.patches(provider,download):
            for _ in range(4):self.step(job)
        self.assertEqual([r['method'] for r in calls],['POST','GET','GET','GET']);self.assertEqual(len(list_assets(job_id=job['id'])),1)

    def test_consumed_edit_reference_can_be_deleted_before_query_and_import(self):
        (self.uploads/'ref.png').write_bytes(PNG)
        job=self.action_job(model='qwen-edit-2511',operation='edit',reference_images=['/uploads/ref.png'])
        provider,download,calls=self.clients()
        with self.patches(provider,download):self.step(job);(self.uploads/'ref.png').unlink();self.step(job);self.step(job)
        self.assertEqual(get_job(job['id'])['status'],'succeeded');self.assertEqual(len(calls),3)

    def test_key_change_waits_without_http_and_restoration_queries_original_task(self):
        job=self.action_job();provider,download,calls=self.clients()
        with self.patches(provider,download):
            self.step(job);update_provider_runtime_config('modelscope',api_key='fixture-new-key')
            self.assertEqual(self.step(job)['status'],'scheduled');self.assertEqual(len(calls),1)
            update_provider_runtime_config('modelscope',api_key=KEY);self.step(job);self.step(job)
        self.assertEqual(get_job(job['id'])['status'],'succeeded');self.assertEqual(len(calls),3)

    def test_endpoint_change_cannot_query_with_the_wrong_identity(self):
        job=self.action_job();provider,download,calls=self.clients()
        with self.patches(provider,download):self.step(job);update_provider_runtime_config('modelscope',base_url_override='https://other.example.test');self.step(job)
        self.assertEqual(len(calls),1);self.assertEqual(get_job(job['id'])['stage'],'image_poll')

    def test_unrelated_provider_key_change_does_not_block_selected_followup(self):
        for name in ('agnes_image','bytedance','pollinations','openai_image'):update_provider_runtime_config(name,enabled=False)
        job=self.job(self.propose(request={'prompt':'山谷'}))
        async def reject(*args):raise BackendUnavailable('fixture rejected')
        self.runtime,self.worker=_runtime({'siliconflow':SimpleNamespace(generate=reject),'modelscope':ModelScopeProvider()})
        provider,download,calls=self.clients()
        with self.patches(provider,download):self.step(job);update_provider_runtime_config('siliconflow',api_key='fixture-other-key');self.step(job);self.step(job)
        self.assertEqual(get_job(job['id'])['status'],'succeeded');self.assertEqual(len(calls),3)

    def test_payload_tampering_before_followup_stops_http(self):
        job=self.action_job();provider,download,calls=self.clients()
        with self.patches(provider,download):
            self.step(job)
            with db_transaction(immediate=True) as conn:
                body=json.loads(get_job(job['id'],conn=conn)['input_json']);body['request']['prompt']='被修改'
                conn.execute('UPDATE jobs SET input_json=? WHERE id=?',(json.dumps(body),job['id']))
            self.step(job)
        self.assertEqual(len(calls),1);self.assertEqual(get_job(job['id'])['status'],'failed')

    def test_execution_model_tampering_is_not_hidden_by_immutable_plan_digest(self):
        job=self.action_job();provider,download,calls=self.clients()
        with self.patches(provider,download):
            self.step(job)
            with db_transaction(immediate=True) as conn:conn.execute('UPDATE jobs SET model=? WHERE id=?',('wrong-model',job['id']))
            self.step(job)
        self.assertEqual(len(calls),1);self.assertEqual(get_job(job['id'])['error_code'],'image_task_identity_changed')

    def test_local_timeout_retains_upstream_identity_and_observed_state(self):
        job=self.action_job();provider,download,calls=self.clients()
        with self.patches(provider,download):
            self.step(job);self.worker.async_worker.now=lambda:datetime.now(timezone.utc)+timedelta(days=2);self.step(job)
        current=get_job(job['id']);self.assertEqual(current['error_category'],'local_timeout');self.assertEqual(current['provider_status'],'submitted')
        self.assertEqual(current['external_task_id'],'image-task-1');self.assertFalse(regeneration_control(current)['enabled']);self.assertEqual(len(calls),1)

    def test_only_observed_failed_task_allows_new_confirmed_proposal(self):
        job=self.action_job();provider,download,calls=self.clients([{'task_id':'task-1'},{'task_status':'FAILED'}])
        with self.patches(provider,download):self.step(job);self.step(job)
        current=get_job(job['id']);self.assertEqual(current['error_code'],'image_generation_failed');self.assertTrue(regeneration_control(current)['enabled'])
        self.assertEqual(accepted_image_task(job['id'])['provider_status'],'FAILED');self.assertEqual(len(calls),2)

    def test_dispatch_transaction_failure_keeps_receipt_and_replay_schedules_without_post(self):
        job=self.action_job();provider,download,calls=self.clients();original=self.worker.async_worker._dispatch
        def fail(conn,message,stage,*args,**kwargs):
            if stage=='image_poll':raise RuntimeError('fixture outbox failure')
            return original(conn,message,stage,*args,**kwargs)
        with self.patches(provider,download):
            with patch.object(self.worker.async_worker,'_dispatch',side_effect=fail),self.assertRaises(BackendUnavailable):self.step(job)
            self.assertEqual(accepted_image_task(job['id'])['task_id'],'image-task-1')
            self.assertEqual(get_job(job['id'])['stage'],'image_generate')
            self.step(job);self.step(job);self.step(job)
        self.assertEqual(get_job(job['id'])['status'],'succeeded');self.assertEqual(sum(r['method']=='POST' for r in calls),1)

    def test_receipt_identity_and_task_binding_cannot_be_replaced_or_deleted(self):
        job=self.action_job();provider,download,_=self.clients()
        with self.patches(provider,download):self.step(job)
        with closing(db_connect()) as conn:
            for sql in ('DELETE FROM image_task_receipts','UPDATE image_task_receipts SET model="wrong"','UPDATE image_task_receipts SET task_id="wrong"',
                        'INSERT OR REPLACE INTO image_task_receipts SELECT * FROM image_task_receipts'):
                with self.subTest(sql=sql),self.assertRaises(sqlite3.IntegrityError):conn.execute(sql)
            run_migrations(conn);self.assertEqual(len(image_task_receipts(job['id'],conn=conn)),1)

    def test_pending_output_is_private_and_not_copied_into_public_job_summary(self):
        job=self.action_job();provider,download,_=self.clients([{'task_id':'task-1'},{'task_status':'SUCCEED','output_images':['https://cdn.example.test/out.png?signature=fixture-private']}])
        with self.patches(provider,download):self.step(job);self.step(job)
        self.assertNotIn('fixture-private',get_job(job['id'])['output_json']);self.assertNotIn(KEY,json.dumps(image_task_receipts(job['id'])))

    def test_cold_process_after_committed_receipt_resumes_query_and_import_without_post(self):
        job=self.action_job();child=self.spawn(job,'crash_after_receipt');self.assertEqual(child.exitcode,74)
        self.assertEqual(get_job(job['id'])['stage'],'image_poll')
        self.assertEqual(self.spawn(job,'normal').exitcode,0);self.assertEqual(self.spawn(job,'normal').exitcode,0)
        self.assertEqual(self.call_counts(),{'POST':1,'POLL':1,'DOWNLOAD':1});self.assertEqual(len(list_assets(job_id=job['id'])),1)

    def test_cold_process_after_post_without_receipt_becomes_uncertain_without_second_post(self):
        job=self.action_job();self.assertEqual(self.spawn(job,'crash_after_post').exitcode,71)
        self.assertEqual(self.spawn(job,'watchdog').exitcode,0)
        self.assertEqual(get_job(job['id'])['error_code'],'image_submit_ambiguous')
        self.assertEqual(self.call_counts(),{'POST':1,'POLL':0,'DOWNLOAD':0});self.assertEqual(image_task_receipts(job['id'])[0]['state'],'uncertain')

    def test_cold_process_during_query_repeats_get_but_never_submission(self):
        job=self.action_job();self.assertEqual(self.spawn(job,'normal').exitcode,0)
        self.assertEqual(self.spawn(job,'crash_poll').exitcode,72)
        self.assertEqual(self.spawn(job,'normal').exitcode,0);self.assertEqual(self.spawn(job,'normal').exitcode,0)
        self.assertEqual(self.call_counts(),{'POST':1,'POLL':2,'DOWNLOAD':1});self.assertEqual(get_job(job['id'])['status'],'succeeded')

    def test_cold_process_during_download_retries_import_and_registers_one_asset(self):
        job=self.action_job();self.spawn(job,'normal');self.spawn(job,'normal')
        self.assertEqual(self.spawn(job,'crash_import').exitcode,73)
        self.assertEqual(self.spawn(job,'normal').exitcode,0)
        self.assertEqual(self.call_counts(),{'POST':1,'POLL':1,'DOWNLOAD':2});self.assertEqual(len(list_assets(job_id=job['id'])),1)

    def test_concurrent_cold_consumers_submit_only_once(self):
        job=self.action_job();barrier=self.context.Barrier(2);output=self.context.Queue();self.queues.append(output)
        children=[]
        for _ in range(2):
            child=self.context.Process(target=_cold_worker,args=(str(C.DB_FILE),job['id'],'normal',barrier,output));self.children.append(child);children.append(child);child.start()
        results=[output.get(timeout=40) for _ in children]
        for child in children:child.join(10);self.assertEqual(child.exitcode,0)
        self.assertEqual(self.call_counts()['POST'],1);self.assertTrue(any(row['status']=='scheduled' for row in results))

    def test_submission_marker_and_delayed_watchdog_exist_before_http_reply(self):
        job=self.action_job()
        def inspect(request):
            if request.method!='POST':return
            self.assertEqual(image_task_receipts(job['id'])[0]['state'],'prepared')
            with closing(db_connect()) as conn:
                rows=conn.execute('SELECT available_at FROM job_dispatches WHERE job_id=? ORDER BY rowid',(job['id'],)).fetchall()
                self.assertEqual(len(rows),2);self.assertGreater(datetime.fromisoformat(rows[-1][0]),datetime.now(timezone.utc))
        provider,download,_=self.clients(callback=inspect)
        with self.patches(provider,download):self.step(job)

    def test_late_acceptance_is_retained_without_resurrecting_a_terminal_job(self):
        job=self.action_job();original=self.message(job['id'])
        def expire(request):
            if request.method!='POST':return
            runtime,worker=_runtime();worker.async_worker.now=lambda:datetime.now(timezone.utc)+timedelta(days=30)
            self.assertEqual(runtime.handle(original)['status'],'failed')
        provider,download,calls=self.clients(callback=expire)
        with self.patches(provider,download):self.assertEqual(self.step(job)['status'],'late_receipt')
        current=get_job(job['id']);self.assertEqual(current['status'],'failed');self.assertEqual(current['external_task_id'],'image-task-1')
        self.assertEqual(accepted_image_task(job['id'])['task_id'],'image-task-1');self.assertEqual(len(calls),1)

    def test_proxy_change_cannot_take_over_original_task_queries(self):
        job=self.action_job();provider,download,calls=self.clients()
        with self.patches(provider,download):
            self.step(job);update_provider_transport_config('modelscope',transport_mode='explicit_proxy',proxy_url='http://fixture-proxy.example.test:8080');self.step(job)
        self.assertEqual(len(calls),1);self.assertEqual(get_job(job['id'])['error_code'],'action_execution_connection_changed')

    def test_receipt_is_retained_when_terminal_job_is_explicitly_cleared(self):
        job=self.action_job();provider,download,_=self.clients()
        with self.patches(provider,download):
            for _ in range(3):self.step(job)
        with closing(db_connect()) as conn:conn.execute('DELETE FROM jobs WHERE id=?',(job['id'],))
        self.assertIsNone(get_job(job['id']));self.assertEqual(accepted_image_task(job['id'])['task_id'],'image-task-1')

    def test_new_migration_rolls_back_all_receipt_objects_on_failure(self):
        from angemedia_gateway.db.migrations import _image_task_receipts_v1
        with closing(sqlite3.connect(':memory:',isolation_level=None)) as conn:
            conn.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY)');conn.execute('CREATE TABLE assistant_action_binding_key(id INTEGER PRIMARY KEY)')
            def fail(connection):
                _image_task_receipts_v1(connection);raise RuntimeError('fixture migration failure')
            with patch('angemedia_gateway.db.migrations.MIGRATIONS',(('image_task_receipts_v1',fail),)),self.assertRaises(RuntimeError):run_migrations(conn)
            self.assertFalse(any(row[0].startswith('image_task_') for row in conn.execute('SELECT name FROM sqlite_master')))
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0],0)

    def test_old_payload_keeps_legacy_handler_and_is_not_given_an_invented_receipt(self):
        prepared=ImageJobAdmissionService().prepare(ImageRequest(prompt='旧任务',model='qwen')).arguments()
        prepared['payload'].pop('async_image')
        job=ImageJobAdmissionService().admission_service.admit(**prepared).job
        provider,download,calls=self.clients()
        with patch.object(C,'POLL_INTERVAL',0),self.patches(provider,download):self.assertEqual(self.step(job)['status'],'succeeded')
        self.assertEqual(image_task_receipts(job['id']),[]);self.assertEqual(len(calls),3)

    def test_concurrent_cold_importers_register_one_asset(self):
        job=self.action_job();self.spawn(job,'normal');self.spawn(job,'normal')
        barrier=self.context.Barrier(2);output=self.context.Queue();self.queues.append(output);children=[]
        for _ in range(2):
            child=self.context.Process(target=_cold_worker,args=(str(C.DB_FILE),job['id'],'normal',barrier,output));self.children.append(child);children.append(child);child.start()
        results=[output.get(timeout=40) for _ in children]
        for child in children:child.join(10);self.assertEqual(child.exitcode,0)
        self.assertTrue(any(row['status']=='succeeded' for row in results));self.assertEqual(len(list_assets(job_id=job['id'])),1)
        self.assertEqual(self.call_counts()['POST'],1)

    def test_stage_projection_and_both_locales_describe_original_image_task(self):
        observation=assistant_job_observation({'id':'a'*32,'kind':'image','status':'running','stage':'image_poll','external_task_id':'private-id'})
        self.assertEqual(observation['stage'],'image_poll');self.assertNotIn('private-id',json.dumps(observation))
        source=Path(__file__).resolve().parents[1]/'app/www/assets/studio'
        studio=self.temp/'js-contract';(studio/'lib').mkdir(parents=True)
        (studio/'package.json').write_text('{"type":"module"}',encoding='utf-8')
        for name in ('i18n.js','lib/format.js','lib/job-display.js','lib/assistant-media-presentation.js'):
            shutil.copyfile(source/name,studio/name)
        script=f"""
        import assert from 'node:assert/strict';
        const storage=new Map();globalThis.localStorage={{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)}};
        const i=await import({json.dumps((studio/'i18n.js').as_uri())});
        const d=await import({json.dumps((studio/'lib/job-display.js').as_uri())});
        const p=await import({json.dumps((studio/'lib/assistant-media-presentation.js').as_uri())});
        for(const lang of i.supportedLanguages){{
          i.setLanguage(lang);const label=d.displayJobStage('image_poll');
          assert(!label.includes('image_poll'));assert(!label.includes('jobs.stage'));
          assert.equal(p.mediaProgressKey({{status:'running',stage:'image_poll'}}),'image_wait');
          assert.equal(p.mediaProgressKey({{status:'running',stage:'image_import'}}),'saving');
          assert.equal(p.mediaProgressKey({{status:'failed',error_code:'image_poll_timeout'}}),'timeout');
          assert(!p.mediaProgress({{status:'running',stage:'image_poll'}}).includes('assistant.media'));
          assert(!d.displayJobEventType('image_task_late_receipt').includes('image_task'));
          assert(!d.displayJobProviderStatus('SUCCEED').includes('jobs.providerStatus'));
        }}console.log('ok');
        """
        result=subprocess.run(['node','--input-type=module'],input=script,text=True,capture_output=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('ok',result.stdout)

    def test_forged_higher_attempt_cannot_start_a_parallel_submission(self):
        job=self.action_job();message=self.message(job['id']);message['attempt']=2
        def inspect(request):
            if request.method=='POST':self.assertEqual(self.runtime.handle(message)['status'],'inflight')
        provider,download,calls=self.clients(callback=inspect)
        with self.patches(provider,download):self.step(job)
        self.assertEqual(sum(r['method']=='POST' for r in calls),1)
