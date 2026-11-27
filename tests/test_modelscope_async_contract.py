"""单次协议、同步兼容与原动作worker使用真实HTTP序列化的受控合同。"""
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import date
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway import config as C
from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.providers.catalog.policy import policy_api_response
from angemedia_gateway.providers.errors import BackendUnavailable, ProviderError, ProviderProtocolError, ProviderTaskFailed, ProviderValidationError
from angemedia_gateway.providers.image.modelscope import ModelScopeProvider
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.provider_runtime_config import clear_provider_runtime_api_key, update_provider_runtime_config
from angemedia_gateway.schemas import ImageRequest
from angemedia_gateway.services.image_execution import ImageExecutionService
from angemedia_gateway.services.image_execution import InvalidImageRequest
from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService
from angemedia_gateway.services.image_job_worker import ImageJobWorker
from angemedia_gateway.services.job_stage_registry import JobStageRegistry
from angemedia_gateway.services.worker_runtime import WorkerRuntime
from test_siliconflow_qwen_image_contract import PNG, REMOTE_URL

KEY='fixture-modelscope-key-123456789'
MODULE='angemedia_gateway.providers.image.modelscope'


class ModelScopeAsyncContractTest(ActionWorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        for name,value in [('MODELSCOPE_API_KEY',''),('POLL_INTERVAL',0),('MAX_POLL_TIME',30)]:
            item=patch.object(C,name,value); item.start(); self.addCleanup(item.stop)
        item=patch(MODULE+'.quota.try_acquire',new=AsyncMock(return_value=True)); self.budget=item.start(); self.addCleanup(item.stop)
        update_provider_runtime_config('modelscope',enabled=True,api_key=KEY,base_url_override='https://modelscope.example.test')
        self.target=RouteTarget('modelscope','Qwen/Qwen-Image-2512')
        self.req=ImageRequest(prompt='秋日山谷里的小屋',model='qwen')

    def clients(self, responses):
        calls=[]; opened=[]
        def reply(request):
            calls.append({'method':request.method,'url':str(request.url),'authorization':request.headers.get('authorization'),
                          'headers':dict(request.headers),'json':json.loads(request.content) if request.content else None})
            response=responses.pop(0)
            if isinstance(response,BaseException): raise response
            return response if isinstance(response,httpx.Response) else httpx.Response(200,json=response)
        @asynccontextmanager
        async def provider(**kwargs):
            opened.append(kwargs)
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client: yield client
        def download(): return httpx.AsyncClient(transport=httpx.MockTransport(reply))
        return provider,download,calls,opened

    def runtime(self):
        worker=ImageJobWorker(executor=ImageExecutionService(providers={'modelscope':ModelScopeProvider()}))
        return WorkerRuntime(registry=JobStageRegistry({'image_generate':worker.handle,'image_poll':worker.handle_poll,'image_import':worker.handle_import}),runtime_refresher=lambda:None)

    def test_submit_returns_task_id_without_polling_or_sleeping(self):
        provider,_,calls,_=self.clients([{'task_id':'fixture-task-1'}])
        with patch(MODULE+'.provider_client',new=provider),patch(MODULE+'.asyncio.sleep',new=AsyncMock()) as sleep:
            self.assertEqual(asyncio.run(ModelScopeProvider().submit(self.req,self.target)),'fixture-task-1')
        self.assertEqual([r['method'] for r in calls],['POST']); sleep.assert_not_awaited(); self.budget.assert_awaited_once()
        self.assertEqual(calls[0]['json'],{'model':self.target.model,'prompt':self.req.prompt,'n':1,'size':'1024x1024'})
        self.assertEqual(calls[0]['headers']['x-modelscope-async-mode'],'true')
        self.assertEqual(calls[0]['authorization'],'Bearer '+KEY); self.assertNotIn(KEY,calls[0]['url'])

    def test_poll_reads_only_the_task_without_submission_budget(self):
        provider,_,calls,_=self.clients([{'task_id':'fixture-task-1','task_status':'SUCCEED','output_images':[REMOTE_URL]}])
        with patch(MODULE+'.provider_client',new=provider): task=asyncio.run(ModelScopeProvider().poll('fixture-task-1'))
        self.assertEqual((task.task_id,task.status,task.image_url),('fixture-task-1','SUCCEED',REMOTE_URL))
        self.assertEqual([r['method'] for r in calls],['GET']); self.budget.assert_not_awaited()
        self.assertEqual(calls[0]['headers']['x-modelscope-task-type'],'image_generation')
        self.assertNotIn('x-modelscope-async-mode',calls[0]['headers'])
        self.assertNotIn(KEY,repr(task))

    def test_invalid_echoed_task_identity_is_a_safe_protocol_failure(self):
        for echoed in (None, '', 7, 'different-task-private'):
            provider,_,calls,_=self.clients([{'task_id':echoed,'task_status':'SUCCEED','output_images':[REMOTE_URL]}])
            with self.subTest(echoed=echoed),patch(MODULE+'.provider_client',new=provider),self.assertRaises(ProviderProtocolError) as caught:
                asyncio.run(ModelScopeProvider().poll('task-1'))
            self.assertEqual([item['method'] for item in calls],['GET'])
            self.assertNotIn('different-task-private',str(caught.exception))
            self.assertNotIn(REMOTE_URL,str(caught.exception))

    def test_generate_reuses_one_client_and_the_same_single_operation_contract(self):
        provider,_,calls,opened=self.clients([{'task_id':'task-1'},{'task_status':'RUNNING'},{'task_status':'SUCCEED','output_images':[REMOTE_URL]}])
        with patch(MODULE+'.provider_client',new=provider): result=asyncio.run(ModelScopeProvider().generate(self.req,self.target))
        self.assertEqual(result['data'],[{'url':REMOTE_URL}]); self.assertEqual(len(opened),1)
        self.assertEqual([r['method'] for r in calls],['POST','GET','GET']); self.budget.assert_awaited_once()

    def test_base64_and_remote_edits_use_image_url_without_size(self):
        (self.uploads/'ref.png').write_bytes(PNG)
        inline='data:image/png;base64,'+base64.b64encode(PNG).decode()
        for reference,expected in [('/uploads/ref.png',inline),('https://images.example.test/ref.png','https://images.example.test/ref.png')]:
            provider,_,calls,_=self.clients([{'task_id':'edit-1'}])
            req=ImageRequest(prompt='改成秋景',model='qwen-edit-2511',operation='edit',reference_images=[reference])
            with self.subTest(reference=reference),patch(MODULE+'.provider_client',new=provider):
                asyncio.run(ModelScopeProvider().submit(req,RouteTarget('modelscope','Qwen/Qwen-Image-Edit-2511')))
            self.assertEqual(calls[0]['json']['image_url'],[expected]); self.assertNotIn('image',calls[0]['json']); self.assertNotIn('size',calls[0]['json'])
        ref=load_provider_catalog().models_by_id['qwen-edit-2511'].operations['image_edit'].refs[0]
        self.assertEqual(ref.provider_field,'image_url'); self.assertEqual(ref.max_total,10)

    def test_invalid_submission_task_ids_are_protocol_errors_without_get(self):
        for value in (None,False,1,{},[], '', ' task ','.','..','x\ny','x'*257):
            provider,_,calls,_=self.clients([{'task_id':value,'private':KEY}])
            with self.subTest(value=value),patch(MODULE+'.provider_client',new=provider),self.assertRaises(ProviderProtocolError) as caught:
                asyncio.run(ModelScopeProvider().submit(self.req,self.target))
            self.assertEqual(len(calls),1); self.assertNotIn(KEY,str(caught.exception))

    def test_account_visible_qwen_edit_uses_only_single_reference(self):
        provider, _, calls, _ = self.clients([{'task_id': 'edit-1'}])
        target = RouteTarget('modelscope', 'Qwen/Qwen-Image-Edit')
        ref = 'https://images.example.test/reference.png'
        req = ImageRequest(prompt='改成秋景', model='modelscope-qwen-image-edit', operation='edit', image=ref)
        with patch(MODULE + '.provider_client', new=provider):
            asyncio.run(ModelScopeProvider().submit(req, target))
            for invalid in (ImageRequest(prompt='秋景', model=req.model), req.model_copy(update={'reference_images': [ref]})):
                with self.assertRaises(ProviderValidationError):
                    asyncio.run(ModelScopeProvider().submit(invalid, target))
        self.assertEqual([item['json'] for item in calls], [
            {'model': target.model, 'n': 1, 'prompt': '改成秋景', 'image_url': ref},
        ])

    def test_invalid_query_id_is_rejected_before_opening_client(self):
        for value in (None,4,[], '', ' x', '..','a\rb','x'*257):
            with self.subTest(value=value),patch(MODULE+'.provider_client') as client,self.assertRaises(ProviderProtocolError):
                asyncio.run(ModelScopeProvider().poll(value))
            client.assert_not_called()
        self.budget.assert_not_awaited()

    def test_opaque_id_is_encoded_as_one_path_segment(self):
        identity='任务/a?b#c%2F'
        provider,_,calls,_=self.clients([{'task_status':'RUNNING'}])
        with patch(MODULE+'.provider_client',new=provider): task=asyncio.run(ModelScopeProvider().poll(identity))
        self.assertEqual(task.task_id,identity)
        self.assertEqual(calls[0]['url'],'https://modelscope.example.test/v1/tasks/%E4%BB%BB%E5%8A%A1%2Fa%3Fb%23c%252F')

    def test_pending_and_unknown_nonempty_statuses_are_not_inferred_terminal(self):
        for status in ('PENDING','RUNNING','WAITING_FUTURE_STATE'):
            provider,_,_,_=self.clients([{'task_status':status,'output_images':[REMOTE_URL]}])
            with self.subTest(status=status),patch(MODULE+'.provider_client',new=provider): task=asyncio.run(ModelScopeProvider().poll('task-1'))
            self.assertEqual(task.status,status); self.assertIsNone(task.image_url)

    def test_invalid_status_shapes_are_safe_protocol_errors(self):
        for status in (None,1,[],{},'', ' RUNNING','RUNNING\n','x'*65):
            provider,_,_,_=self.clients([{'task_status':status,'error':KEY}])
            with self.subTest(status=status),patch(MODULE+'.provider_client',new=provider),self.assertRaises(ProviderProtocolError) as caught:
                asyncio.run(ModelScopeProvider().poll('task-1'))
            self.assertNotIn(KEY,str(caught.exception))

    def test_succeeded_requires_a_typed_http_image_url(self):
        for images in (None,[],REMOTE_URL,[None],[{}],[1],['file:///private.png'],['https://'],['https://user:secret@example.test/out.png'],[' '+REMOTE_URL]):
            provider,_,_,_=self.clients([{'task_status':'SUCCEED','output_images':images}])
            with self.subTest(images=images),patch(MODULE+'.provider_client',new=provider),self.assertRaises(ProviderProtocolError):
                asyncio.run(ModelScopeProvider().poll('task-1'))

    def test_single_query_failed_state_preserves_fact_without_raw_error(self):
        provider,_,_,_=self.clients([{'task_status':'FAILED','errors':KEY,'output_images':[REMOTE_URL]}])
        with patch(MODULE+'.provider_client',new=provider): task=asyncio.run(ModelScopeProvider().poll('task-1'))
        self.assertEqual(task.status,'FAILED'); self.assertIsNone(task.image_url); self.assertNotIn(KEY,repr(task))

    def test_generate_reports_upstream_failure_as_task_failure(self):
        provider,_,calls,_=self.clients([{'task_id':'task-1'},{'task_status':'FAILED','errors':KEY}])
        with patch(MODULE+'.provider_client',new=provider),self.assertRaises(ProviderTaskFailed) as caught:
            asyncio.run(ModelScopeProvider().generate(self.req,self.target))
        self.assertEqual(caught.exception.provider_status,'FAILED'); self.assertEqual(caught.exception.error_category,'task_failed')
        self.assertFalse(caught.exception.retryable); self.assertNotIn(KEY,str(caught.exception)); self.assertEqual(len(calls),2)

    def test_single_operations_have_no_hidden_http_retry(self):
        for status in (401,403,429,500):
            for operation in ('submit','poll'):
                provider,_,calls,_=self.clients([httpx.Response(status,json={'private':KEY})])
                with self.subTest(status=status,operation=operation),patch(MODULE+'.provider_client',new=provider),self.assertRaises(ProviderError) as caught:
                    api=ModelScopeProvider(); asyncio.run(api.submit(self.req,self.target) if operation=='submit' else api.poll('task-1'))
                self.assertEqual(caught.exception.status_code,status); self.assertNotIn(KEY,str(caught.exception)); self.assertEqual(len(calls),1)

    def test_missing_key_stops_both_operations_before_http_and_materializing_refs(self):
        clear_provider_runtime_api_key('modelscope')
        with patch(MODULE+'.provider_client') as client,patch(MODULE+'.prepare_builtin_reference') as reference:
            for operation in ('submit','poll'):
                with self.subTest(operation=operation),self.assertRaises(BackendUnavailable) as caught:
                    api=ModelScopeProvider(); asyncio.run(api.submit(self.req,self.target) if operation=='submit' else api.poll('task-1'))
                self.assertEqual(caught.exception.error_category,'auth')
        client.assert_not_called(); reference.assert_not_called(); self.budget.assert_not_awaited()

    def test_monotonic_deadline_caps_sleep_and_does_not_start_an_overdue_query(self):
        provider,_,calls,_=self.clients([{'task_id':'task-1'}])
        with patch(MODULE+'.provider_client',new=provider),patch.object(C,'MAX_POLL_TIME',10),patch.object(C,'POLL_INTERVAL',50),patch(MODULE+'.time',new=SimpleNamespace(monotonic=Mock(side_effect=[100,101,110]))),patch(MODULE+'.asyncio.sleep',new=AsyncMock()) as sleep,self.assertRaises(BackendUnavailable):
            asyncio.run(ModelScopeProvider().generate(self.req,self.target))
        sleep.assert_awaited_once_with(9); self.assertEqual([r['method'] for r in calls],['POST'])

    def test_cancelled_wait_does_not_poll_or_resubmit(self):
        provider,_,calls,_=self.clients([{'task_id':'task-1'}])
        with patch(MODULE+'.provider_client',new=provider),patch(MODULE+'.asyncio.sleep',new=AsyncMock(side_effect=asyncio.CancelledError)),self.assertRaises(asyncio.CancelledError):
            asyncio.run(ModelScopeProvider().generate(self.req,self.target))
        self.assertEqual([r['method'] for r in calls],['POST']); self.budget.assert_awaited_once()

    def test_confirmed_edit_uses_original_worker_and_one_asset_with_http_free_replay(self):
        (self.uploads/'ref.png').write_bytes(PNG)
        req=ImageRequest(prompt='背景变成秋天',model='qwen-edit-2511',operation='edit',reference_images=['/uploads/ref.png'])
        action=self.propose(request=req.model_dump(exclude_unset=True)); self.assertEqual(self.counts(),(0,0,0))
        job=self.job(action); message=self.message(job['id'])
        provider,download,calls,_=self.clients([{'task_id':'task-edit-1'},{'task_status':'SUCCEED','output_images':[REMOTE_URL]},httpx.Response(200,content=PNG,headers={'content-type':'image/png'})])
        with patch(MODULE+'.provider_client',new=provider),patch('angemedia_gateway.media._remote_media_http_client',new=download):
            runtime=self.runtime(); self.assertEqual(runtime.handle(message)['status'],'scheduled')
            self.assertEqual(runtime.handle(self.message(job['id']))['status'],'scheduled')
            self.assertEqual(runtime.handle(self.message(job['id']))['status'],'succeeded')
            self.assertEqual(runtime.handle(message)['status'],'duplicate')
        assets=list_assets(job_id=job['id']); self.assertEqual(len(assets),1); self.assertEqual((self.output/assets[0]['relative_path']).read_bytes(),PNG)
        self.assertEqual([r['method'] for r in calls],['POST','GET','GET']); self.assertIsNone(calls[-1]['authorization'])
        self.assertIn('image_url',calls[0]['json']); self.assertNotIn('image',calls[0]['json'])
        self.observation={'fixture_only':True,'actual_generation_or_llm_calls':0,'production_database_read':False,'jobs_before_confirmation':0,'provider_posts':1,'provider_polls':1,'safe_downloads':1,'assets':1,'replay':'duplicate','base64_field':'image_url','original_confirmation_worker_localizer_assets':True,'durable_restart_recovery_verified':False}

    def test_current_policy_review_is_expiring_public_evidence_not_account_proof(self):
        policy=load_provider_catalog().providers_by_id['modelscope'].policy
        current=policy_api_response(policy,as_of=date.fromisoformat(policy.reviewed_at))
        expired=policy_api_response(policy,as_of=date.fromisoformat(policy.review_after))
        self.assertEqual(current['billing'],'usage_credits'); self.assertIn('magicube_balance',current['account_requirements'])
        self.assertFalse(current['account_access_verified']); self.assertFalse(current['generation_verified']); self.assertEqual(expired['review_status'],'review_due')

    def test_implicit_unused_edit_size_survives_canonical_round_trip(self):
        (self.uploads/'ref.png').write_bytes(PNG)
        req=ImageRequest(prompt='改成秋景',model='qwen-edit-2511',reference_images=['/uploads/ref.png'])
        first=ImageJobAdmissionService().prepare(req).arguments()
        self.assertNotIn('size',first['payload']['request'])
        second=ImageJobAdmissionService().prepare(ImageRequest.model_validate(first['payload']['request'])).arguments()
        self.assertEqual(first,second)

    def test_explicit_unsupported_edit_size_is_not_silently_dropped(self):
        (self.uploads/'ref.png').write_bytes(PNG)
        req=ImageRequest(prompt='改成秋景',model='qwen-edit-2511',size='1024x1024',reference_images=['/uploads/ref.png'])
        with self.assertRaises(InvalidImageRequest): ImageJobAdmissionService().prepare(req)
        self.assertEqual(self.counts(),(0,0,0))

    def test_generation_keeps_its_effective_default_size_in_saved_payload(self):
        first=ImageJobAdmissionService().prepare(self.req).arguments()
        self.assertEqual(first['payload']['request']['size'],'1024x1024')
        second=ImageJobAdmissionService().prepare(ImageRequest.model_validate(first['payload']['request'])).arguments()
        self.assertEqual(first,second)
