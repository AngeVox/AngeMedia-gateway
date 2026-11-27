"""默认候选和匿名兼容服务共用准备、确认、grant与实际HTTP，不调用真实供应商。"""
from contextlib import asynccontextmanager, closing, ExitStack
import json
import unittest
from unittest.mock import patch

import httpx

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway import config as C
from angemedia_gateway.assistant_action_contract import ActionError
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.repositories.settings import set_config, upsert_custom_provider, update_custom_provider_details
from angemedia_gateway.schemas import ImageRequest
from angemedia_gateway.services.image_execution import build_image_execution_plan, NoImageProviderAvailable, InvalidImageRequest
from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService, parse_image_job_payload
from test_modelscope_durable_pipeline import _runtime
from test_siliconflow_qwen_image_contract import PNG, REMOTE_URL


class ImageAdmissionConfigurationTest(ActionWorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        item=patch.multiple(C,SILICONFLOW_API_KEY='',AGNES_API_KEY='',MODELSCOPE_API_KEY='')
        item.start();self.addCleanup(item.stop)
        for provider in ('siliconflow','modelscope','agnes_image','agnes_video','openai_image','bytedance','pollinations'):
            update_provider_runtime_config(provider,enabled=provider in {'siliconflow','modelscope'},
                api_key='fixture-admission-key' if provider=='modelscope' else None,
                **({'base_url_override':'https://modelscope.example.test'} if provider=='modelscope' else {}))

    def anonymous(self):
        return upsert_custom_provider({'id':'anonymous','name':'Anonymous','base_url':'https://anonymous.example.test/v1',
            'default_model':'test-image','enabled':True})

    def action(self, **values):
        self.anonymous()
        return self.propose(request={'prompt':'山谷','model':'custom:anonymous',**values})

    def clients(self):
        calls=[]
        def reply(request):
            calls.append({'method':request.method,'url':str(request.url),'authorization':request.headers.get('authorization'),
                'json':json.loads(request.content) if request.content else None})
            if request.method=='POST':
                return httpx.Response(200,json={'task_id':'admission-task'} if request.url.host=='modelscope.example.test'
                    else {'data':[{'url':REMOTE_URL}]})
            if '/v1/tasks/' in request.url.path:
                return httpx.Response(200,json={'task_status':'SUCCEED','output_images':[REMOTE_URL]})
            return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
        @asynccontextmanager
        async def provider(**kwargs):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:yield client
        stack=ExitStack()
        for module in ('angemedia_gateway.providers.custom','angemedia_gateway.providers.image.modelscope'):
            stack.enter_context(patch(module+'.provider_client',new=provider))
        stack.enter_context(patch('angemedia_gateway.media._remote_media_http_client',new=lambda:httpx.AsyncClient(transport=httpx.MockTransport(reply))))
        return stack,calls

    def test_default_plan_retains_only_configured_candidates_in_original_order(self):
        plan=build_image_execution_plan(ImageRequest(prompt='山谷'))
        self.assertEqual(plan.routes,tuple(('modelscope',name) for name in ('Qwen/Qwen-Image-2512',
            'black-forest-labs/FLUX.1-Krea-dev','Tongyi-MAI/Z-Image','Tongyi-MAI/Z-Image-Turbo')))
        action=self.propose(request={'prompt':'山谷'})
        self.assertTrue(action['review']['allowed'])
        self.assertEqual([(r['provider'],r['model']) for r in action['review']['routes']],list(plan.routes))

    def test_manual_and_agent_preparation_bind_the_same_filtered_plan(self):
        from angemedia_gateway.services.assistant_action_preparation import ActionPreparer
        direct=ImageJobAdmissionService().prepare(ImageRequest(prompt='山谷')).arguments()
        candidate=ActionPreparer(C.DB_FILE).prepare('image',{'prompt':'山谷'}).body()['admission']
        self.assertEqual(direct['payload']['route'],candidate['payload']['route'])
        self.assertEqual(direct['request_hash'],candidate['request_hash'])
        self.assertEqual(direct['request_hash_version'],candidate['request_hash_version'])

    def test_all_unconfigured_defaults_fail_before_proposal_or_job_creation(self):
        update_provider_runtime_config('modelscope',api_key='')
        with self.assertRaises(NoImageProviderAvailable):build_image_execution_plan(ImageRequest(prompt='山谷'))
        self.assert_code('action_provider_unavailable',self.propose,request={'prompt':'山谷'})
        self.assertEqual(self.counts(),(0,0,0))

    def test_bad_parameters_are_not_misreported_as_missing_credentials(self):
        chain=lambda _:[RouteTarget('modelscope','Qwen/Qwen-Image-2512')]
        update_provider_runtime_config('modelscope',api_key='')
        with self.assertRaises(InvalidImageRequest):
            build_image_execution_plan(ImageRequest(prompt='山谷',guidance=4),resolve_chain_func=chain)

    def test_environment_fallback_can_supply_a_default_candidate(self):
        with patch.object(C,'SILICONFLOW_API_KEY','fixture-environment-key'):
            self.assertEqual(build_image_execution_plan(ImageRequest(prompt='山谷')).routes[0],('siliconflow','Kwai-Kolors/Kolors'))

    def test_explicit_missing_key_is_reviewed_without_substituting_model(self):
        action=self.propose(request={'prompt':'山谷','model':'kolors'})
        self.assertFalse(action['review']['allowed']);self.assertIn('provider_credentials_missing',action['review']['blocked_reasons'])
        self.assertEqual([r['provider'] for r in action['review']['routes']],['siliconflow'])

    def test_new_default_candidate_before_confirmation_requires_fresh_review(self):
        action=self.propose(request={'prompt':'山谷'})
        update_provider_runtime_config('siliconflow',api_key='fixture-added-key')
        self.assert_code('action_environment_changed',self.confirm,action)
        self.assertEqual(self.counts(),(0,0,0))

    def test_default_route_cannot_expand_or_change_after_confirmation(self):
        for provider,value in (('siliconflow','fixture-added-key'),('modelscope','')):
            with self.subTest(provider=provider):
                update_provider_runtime_config('siliconflow',api_key='')
                update_provider_runtime_config('modelscope',api_key='fixture-admission-key')
                job=self.job(self.propose(request={'prompt':'山谷'}));update_provider_runtime_config(provider,api_key=value)
                runtime,_=_runtime();stack,calls=self.clients()
                with stack:self.assert_rejected(runtime,job)
                self.assertEqual(calls,[])

    def test_old_queued_route_is_parsed_without_removing_missing_key_target(self):
        payload={'schema_version':1,'request':{'prompt':'山谷'},'route':{'mode':'builtin','routes':[
            {'provider':'siliconflow','model':'Kwai-Kolors/Kolors'},{'provider':'modelscope','model':'Qwen/Qwen-Image-2512'}]}}
        _,plan=parse_image_job_payload(payload)
        self.assertEqual([p for p,_ in plan.routes],['siliconflow','modelscope'])

    def test_anonymous_proposal_still_requires_cost_and_unverified_acknowledgements(self):
        action=self.action();self.assertTrue(action['review']['allowed'])
        self.assertEqual(set(action['review']['required_acknowledgements']),{'may_incur_cost','unverified_model'})
        self.assert_code('action_acknowledgements_required',self.service.confirm,action['id'],
            self.confirmation(action,acknowledgements=[]),access=self.alice)
        self.assertEqual(self.counts(),(0,0,0))

    def test_paid_or_assistant_policy_still_blocks_anonymous_proposal(self):
        for name,reason in (('ANGE_ASSISTANT_ALLOW_PAID','paid_generation_disabled'),('ANGE_ASSISTANT_ENABLED','assistant_disabled')):
            with self.subTest(name=name):
                set_config(name,'false');action=self.action();self.assertFalse(action['review']['allowed'])
                self.assertIn(reason,action['review']['blocked_reasons'])
                self.assert_code('action_policy_denied',self.confirm,action);set_config(name,'true')
        self.assertEqual(self.counts(),(0,0,0))

    def test_anonymous_confirmation_remains_account_owned(self):
        action=self.action()
        with self.assertRaises(ActionError):self.confirm(action,access=self.bob)
        self.assertEqual(self.counts(),(0,0,0))

    def test_custom_endpoint_must_pass_existing_policy_before_any_admission(self):
        for endpoint in ('not-a-url','http://169.254.169.254/v1','https://user:secret@example.com/v1'):
            with self.subTest(endpoint=endpoint):
                self.anonymous()
                with closing(db_connect()) as conn:
                    conn.execute('UPDATE custom_providers SET base_url=? WHERE id=?',(endpoint,'anonymous'));conn.commit()
                with self.assertRaises(NoImageProviderAvailable):
                    ImageJobAdmissionService().prepare(ImageRequest(prompt='山谷',model='custom:anonymous'))
                self.assert_code('action_provider_unavailable',self.propose,request={'prompt':'山谷','model':'custom:anonymous'})
        self.assertEqual(self.counts(),(0,0,0))

    def test_anonymous_missing_default_requires_real_model_but_accepts_explicit_override(self):
        self.anonymous()
        with closing(db_connect()) as conn:
            conn.execute("UPDATE custom_providers SET default_model='' WHERE id='anonymous'");conn.commit()
        with self.assertRaises(InvalidImageRequest):
            ImageJobAdmissionService().prepare(ImageRequest(prompt='山谷',model='custom:anonymous'))
        action=self.propose(request={'prompt':'山谷','model':'custom:anonymous','provider_model':'explicit-image'})
        self.assertTrue(action['review']['allowed']);self.assertEqual(action['review']['routes'][0]['model'],'explicit-image')

    def test_anonymous_connection_or_enable_change_after_confirmation_stops_http(self):
        for change in ({'api_key':'fixture-now-authenticated'},{'base_url':'https://changed.example.test/v1'},{'enabled':False}):
            with self.subTest(field=next(iter(change))):
                job=self.job(self.action());update_custom_provider_details('anonymous',change)
                runtime,_=_runtime();stack,calls=self.clients()
                with stack:self.assert_rejected(runtime,job)
                self.assertEqual(calls,[])

    def test_anonymous_proxy_change_after_confirmation_stops_http(self):
        from angemedia_gateway.repositories.provider_transport_config import update_provider_transport_config
        job=self.job(self.action());update_provider_transport_config('anonymous',transport_mode='proxy',proxy_url='http://127.0.0.1:18888')
        runtime,_=_runtime();stack,calls=self.clients()
        with stack:self.assert_rejected(runtime,job)
        self.assertEqual(calls,[])

    def test_real_anonymous_confirmation_worker_download_and_replay_register_one_asset(self):
        job=self.job(self.action());message=self.message(job['id']);runtime,_=_runtime();stack,calls=self.clients()
        with stack:
            self.assertEqual(runtime.handle(message)['status'],'succeeded')
            self.assertEqual(runtime.handle(message)['status'],'duplicate')
        self.assertEqual([r['method'] for r in calls],['POST','GET']);self.assertIsNone(calls[0]['authorization']);self.assertIsNone(calls[1]['authorization'])
        self.assertEqual(calls[0]['json']['model'],'test-image');self.assertEqual(len(list_assets(job_id=job['id'])),1)
        self.assertEqual(get_job(job['id'])['status'],'succeeded')
        self.observation={'fixture_only':True,'case':'anonymous_custom','actual_provider_or_llm_calls':0,'posts':1,'downloads':1,'assets':1,'replay_http_calls':0,'authorization_header':False}

    def test_real_default_filtered_confirmation_uses_original_durable_pipeline_once(self):
        job=self.job(self.propose(request={'prompt':'山谷'}));message=self.message(job['id']);runtime,_=_runtime();stack,calls=self.clients()
        with stack:
            for _ in range(3):runtime.handle(self.message(job['id']))
            self.assertEqual(runtime.handle(message)['status'],'duplicate')
        self.assertEqual([r['method'] for r in calls],['POST','GET','GET']);self.assertEqual(len(list_assets(job_id=job['id'])),1)
        self.assertTrue(all('siliconflow' not in r['url'] for r in calls));self.assertEqual(get_job(job['id'])['status'],'succeeded')
        self.observation={'fixture_only':True,'case':'configured_default','actual_provider_or_llm_calls':0,'posts':1,'polls':1,'downloads':1,'assets':1,'replay_http_calls':0,'missing_key_provider_called':False}
