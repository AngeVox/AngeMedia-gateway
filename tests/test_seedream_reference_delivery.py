"""Seedream参考图贯穿目录校验、真实适配器与已确认worker；仅替换远端HTTP。"""
import asyncio
import base64
from contextlib import asynccontextmanager, closing
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from assistant_action_worker_support import ActionWorkerFixture
import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.media_execution_scope import current_execution_scope
from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.providers.catalog.validation import CatalogOperationValidationError, validate_image_operation_request
from angemedia_gateway.providers.errors import BackendUnavailable, ProviderProtocolError
from angemedia_gateway.providers.image.bytedance import ByteDanceImageProvider, build_bytedance_image_payload
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.repositories.reference_relay_config import update_reference_relay_config
from angemedia_gateway.schemas import ImageRequest
from angemedia_gateway.services.assistant_action_specs import ImageProposalArgs
from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService
from angemedia_gateway.services.image_execution import InvalidImageRequest


class SeedreamReferenceDeliveryTest(ActionWorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        credential = patch.object(C,'BYTEDANCE_API_KEY','fixture-byteplus-credential-123456789')
        credential.start();self.addCleanup(credential.stop)
        update_provider_runtime_config('bytedance',enabled=True,base_url_override='https://ark.example.test/api/v3')

    def references(self, count):
        paths=[]
        for index in range(count):
            name=f'frame-{index}.png';(self.uploads/name).write_bytes(b'\x89PNG\r\n\x1a\nframe-'+str(index).encode())
            paths.append('/uploads/'+name)
        return paths

    def request(self, *, model='seedream-5-lite', **values):
        return ImageRequest(prompt='保留人物与场景一致性',model=model,size='2K',operation='edit',**values)

    def target(self, model='seedream-5-lite'):
        entry=load_provider_catalog().models_by_id[model]
        return RouteTarget(provider=entry.provider,model=entry.provider_model)

    def transport(self, *, result=None):
        calls=[]
        def reply(request):
            calls.append({'url':str(request.url),'authorization':request.headers['Authorization'],
                          'payload':json.loads(request.content)})
            return httpx.Response(200,json=result if result is not None else {'data':[{'url':'https://cdn.example.test/fixture.png'}]})
        @asynccontextmanager
        async def client(**kwargs):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as value:yield value
        return client,calls

    def test_lite_accepts_fourteen_references_through_public_and_native_request_schema(self):
        refs=self.references(14);req=self.request(reference_images=refs)
        validate_image_operation_request(req,[self.target()])
        self.assertEqual(ImageProposalArgs.model_validate({'request':req.model_dump()}).request.reference_images,refs)
        self.assertEqual(len(build_bytedance_image_payload(req,self.target())['image']),14)

    def test_pro_retains_ten_reference_boundary(self):
        refs=self.references(11)
        validate_image_operation_request(self.request(model='seedream-5-pro',reference_images=refs[:10]),[self.target('seedream-5-pro')])
        with self.assertRaises(CatalogOperationValidationError):
            validate_image_operation_request(self.request(model='seedream-5-pro',reference_images=refs),[self.target('seedream-5-pro')])

    def test_total_limit_counts_single_image_and_list_together_without_truncation(self):
        refs=self.references(17)
        for model,limit in (('seedream-5-pro',10),('seedream-5-lite',14)):
            with self.subTest(model=model):
                valid=self.request(model=model,image=refs[0],reference_images=refs[1:limit])
                validate_image_operation_request(valid,[self.target(model)])
                self.assertEqual(len(build_bytedance_image_payload(valid,self.target(model))['image']),limit)
                with self.assertRaises(CatalogOperationValidationError):
                    validate_image_operation_request(self.request(model=model,image=refs[0],reference_images=refs[1:limit+1]),[self.target(model)])
        with self.assertRaises(ValidationError):self.request(reference_images=refs)

    def test_other_models_keep_their_original_operation_reference_limits(self):
        refs=self.references(14)
        for model in ('kolors','agnes-2-5','siliconflow-qwen-edit-2509','seedream-3'):
            with self.subTest(model=model):
                with self.assertRaises(CatalogOperationValidationError):
                    validate_image_operation_request(self.request(model=model,reference_images=refs),[self.target(model)])

    def test_mixed_references_keep_order_and_request_without_extra_publication(self):
        local=self.references(1)[0];inline='data:image/png;base64,iVBORw0KGgo='
        remote='https://cdn.example.test/reference.png'
        req=self.request(image=local,reference_images=[remote,inline]);before=req.model_dump()
        with patch('angemedia_gateway.providers.reference_relay.configured_reference_relay_backend',side_effect=AssertionError('不能上传中转')):
            payload=build_bytedance_image_payload(req,self.target())
        self.assertEqual(payload['image'][1:],[remote,inline]);self.assertTrue(payload['image'][0].startswith('data:image/png;base64,'))
        self.assertEqual(req.model_dump(),before)

    def test_bad_local_image_and_private_url_stop_before_actual_http(self):
        self.references(1);(self.uploads/'bad.png').write_bytes(b'not an image')
        client,calls=self.transport()
        for reference in ('/uploads/bad.png','/uploads/../frame-0.png','/uploads/missing.png','http://127.0.0.1/private.png'):
            with self.subTest(reference=reference),patch('angemedia_gateway.providers.image.bytedance.provider_client',new=client):
                with self.assertRaises(BackendUnavailable):
                    asyncio.run(ByteDanceImageProvider().generate(self.request(image=reference),self.target()))
        self.assertEqual(calls,[])

    def test_real_adapter_sends_inline_data_url_and_keeps_existing_response_shape(self):
        inline='data:image/png;base64,iVBORw0KGgo=';client,calls=self.transport()
        with patch('angemedia_gateway.providers.image.bytedance.provider_client',new=client),patch(
                'angemedia_gateway.providers.reference_relay.configured_reference_relay_backend',side_effect=AssertionError('不应中转')):
            result=asyncio.run(ByteDanceImageProvider().generate(self.request(image=inline,watermark=False,output_format='png'),self.target()))
        self.assertEqual(len(calls),1);self.assertEqual(calls[0]['payload']['image'],inline)
        self.assertFalse(calls[0]['payload']['watermark']);self.assertEqual(calls[0]['payload']['output_format'],'png')
        self.assertEqual(result,{'data':[{'url':'https://cdn.example.test/fixture.png'}]})

    def test_oversized_request_is_rejected_before_job_creation_or_provider_call(self):
        refs=self.references(15);before=self.counts()
        with self.assertRaises(InvalidImageRequest):
            ImageJobAdmissionService().prepare(self.request(image=refs[0],reference_images=refs[1:]))
        self.assertEqual(self.counts(),before)

    def test_durable_jobs_still_reject_inline_binary_and_remote_input(self):
        for value in ('data:image/png;base64,iVBORw0KGgo=','https://cdn.example.test/ref.png'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                ImageJobAdmissionService().prepare(self.request(image=value))
        self.assertEqual(self.counts(),(0,0,0))

    def test_agent_fourteen_image_confirmation_executes_once_without_relay_dependency(self):
        refs=self.references(14);action=self.propose(request=self.request(reference_images=refs).model_dump(exclude_none=True))
        self.assertTrue(action['review']['allowed']);self.assertEqual(self.counts(),(0,0,0))
        self.assertIn('may_incur_cost',action['review']['required_acknowledgements'])
        job=self.job(action);first=self.message(job['id']);client,calls=self.transport()
        with closing(db_connect()) as conn:
            grant=dict(conn.execute('SELECT * FROM assistant_action_execution_grants WHERE job_id=?',(job['id'],)).fetchone())
        self.assertNotIn('reference_relay',grant['connections_json'])
        update_reference_relay_config(mode='external_http',upload_url='https://unused-relay.invalid/upload',token='fixture-unused-token')
        runtime=self.image_runtime(providers={'bytedance':ByteDanceImageProvider()})
        with patch('angemedia_gateway.providers.image.bytedance.provider_client',new=client),patch(
                'angemedia_gateway.providers.reference_relay.configured_reference_relay_backend',side_effect=AssertionError('不能中转')):
            self.assertEqual(runtime.handle(first)['status'],'succeeded')
            self.assertEqual(runtime.handle(first)['status'],'duplicate')
        self.assertEqual(len(calls),1);self.assertEqual(len(calls[0]['payload']['image']),14)
        self.assertEqual(len(list_assets(job_id=job['id'])),1);self.assertIsNone(current_execution_scope())
        with closing(db_connect()) as conn:
            evidence='\n'.join(str(dict(row)) for table in ('jobs','job_events','assistant_actions','assistant_action_execution_grants','job_attempts')
                               for row in conn.execute('SELECT * FROM '+table))
        self.assertNotIn('data:image/',evidence);self.assertNotIn('fixture-byteplus-credential',evidence)
        self.assertNotIn('fixture-unused-token',evidence)
        self.observation={'fixture_only':True,'production_database_read':False,'actual_provider_or_llm_calls':0,
            'jobs_before_confirmation':0,'reference_images':14,'provider_posts':len(calls),'relay_calls':0,
            'assets':len(list_assets(job_id=job['id'])),'terminal_replay':'duplicate','source_binary_persisted':False,
            'requires_paid_acknowledgement':True,'execution_scope_released':True}

    def test_confirmed_byteplus_freezes_all_images_and_original_account_after_call_starts(self):
        refs=self.references(14);job=self.job(self.propose(request=self.request(reference_images=refs).model_dump(exclude_none=True)))
        client,calls=self.transport();scopes=[]
        async def generate(req,target):
            scopes.append(current_execution_scope())
            for reference in refs:(self.uploads/reference.rsplit('/',1)[1]).write_bytes(b'\x89PNG\r\n\x1a\nchanged')
            update_provider_runtime_config('bytedance',api_key='fixture-rotated-account',base_url_override='https://changed.invalid/v3')
            return await ByteDanceImageProvider().generate(req,target)
        runtime=self.image_runtime(providers={'bytedance':SimpleNamespace(generate=generate)})
        with patch('angemedia_gateway.providers.image.bytedance.provider_client',new=client):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'],'succeeded')
        self.assertEqual(calls[0]['url'],'https://ark.example.test/api/v3/images/generations')
        self.assertEqual(calls[0]['authorization'],'Bearer fixture-byteplus-credential-123456789')
        decoded=[base64.b64decode(value.partition(',')[2]) for value in calls[0]['payload']['image']]
        self.assertEqual(decoded,[b'\x89PNG\r\n\x1a\nframe-'+str(i).encode() for i in range(14)])
        self.assertTrue(scopes[0]._closed);self.assertEqual(scopes[0]._references,{})

    def test_changed_reference_before_worker_starts_never_contacts_provider(self):
        refs=self.references(14);job=self.job(self.propose(request=self.request(reference_images=refs).model_dump(exclude_none=True)))
        (self.uploads/'frame-13.png').write_bytes(b'\x89PNG\r\n\x1a\nchanged')
        client,calls=self.transport()
        with patch('angemedia_gateway.providers.image.bytedance.provider_client',new=client):
            self.assert_rejected(self.image_runtime(providers={'bytedance':ByteDanceImageProvider()}),job)
        self.assertEqual(calls,[]);self.assertNotEqual(get_job(job['id'])['status'],'succeeded')

    def test_flash_catalog_has_independent_identity_with_shared_pro_contract(self):
        from angemedia_gateway.providers.catalog.api import catalog_api_response
        catalog=load_provider_catalog();flash=catalog.models_by_id['seedream-5-flash'];pro=catalog.models_by_id['seedream-5-pro']
        self.assertEqual(flash.provider_model,'dola-seedream-5-0-flash-260915')
        self.assertEqual(flash.operations,pro.operations);self.assertEqual(flash.size,pro.size)
        self.assertIsNot(flash.operations,pro.operations)
        self.assertIsNot(flash.operations['image_edit'].params,pro.operations['image_edit'].params)
        self.assertIsNone(flash.default_chain_order);self.assertFalse(catalog.providers_by_id['bytedance'].enabled_default)
        projected=next(x for x in catalog_api_response(catalog)['models'] if x['id']==flash.id)
        self.assertFalse(projected['policy']['generation_verified'])

    def test_flash_explicit_selectors_resolve_without_joining_default_fallback(self):
        from angemedia_gateway.routing import resolve_chain
        for selector in ('seedream-5-flash','seedream-flash','dola-seedream-5-0-flash-260915','bytedance::seedream-5-flash'):
            with self.subTest(selector=selector):self.assertEqual(resolve_chain(selector),[self.target('seedream-5-flash')])
        self.assertEqual([x.id for x in load_provider_catalog().default_image_chain()],['kolors','qwen','flux','z-image','z-turbo'])
        update_provider_runtime_config('bytedance',enabled=False)
        self.assertEqual(resolve_chain('seedream-flash'),[])

    def test_flash_accepts_documented_named_and_pixel_sizes(self):
        ref=self.references(1)[0]
        for size in ('1K','1.5K','2K','960x960','2048x2048','3840x240'):
            with self.subTest(size=size):
                req=self.request(model='seedream-5-flash',image=ref).model_copy(update={'size':size})
                validate_image_operation_request(req,[self.target('seedream-5-flash')])
                self.assertEqual(build_bytedance_image_payload(req,self.target('seedream-5-flash'))['size'],size)

    def test_flash_rejects_unapproved_tiers_pixel_counts_and_ratios(self):
        ref=self.references(1)[0]
        for size in ('3K','4K','2.5K','auto','512x512','4096x4096','8192x128'):
            with self.subTest(size=size):
                with self.assertRaises(CatalogOperationValidationError):
                    validate_image_operation_request(self.request(model='seedream-5-flash',image=ref).model_copy(update={'size':size}),[self.target('seedream-5-flash')])

    def test_one_and_half_k_is_shared_with_pro_but_not_granted_to_lite(self):
        ref=self.references(1)[0]
        validate_image_operation_request(self.request(model='seedream-5-pro',image=ref).model_copy(update={'size':'1.5K'}),[self.target('seedream-5-pro')])
        with self.assertRaises(CatalogOperationValidationError):
            validate_image_operation_request(self.request(image=ref).model_copy(update={'size':'1.5K'}),[self.target()])

    def test_flash_ten_image_limit_counts_all_reference_fields(self):
        refs=self.references(11)
        req=self.request(model='seedream-5-flash',image=refs[0],reference_images=refs[1:10])
        validate_image_operation_request(req,[self.target('seedream-5-flash')])
        self.assertEqual(len(build_bytedance_image_payload(req,self.target('seedream-5-flash'))['image']),10)
        with self.assertRaises(CatalogOperationValidationError):
            validate_image_operation_request(req.model_copy(update={'reference_images':refs[1:]}),[self.target('seedream-5-flash')])

    def test_flash_real_text_generation_uses_its_version_and_preserves_controls(self):
        client,calls=self.transport();req=ImageRequest(prompt='蓝色山谷',model='seedream-5-flash',size='1.5K',watermark=False,output_format='jpeg')
        with patch('angemedia_gateway.providers.image.bytedance.provider_client',new=client):
            result=asyncio.run(ByteDanceImageProvider().generate(req,self.target('seedream-5-flash')))
        self.assertEqual(calls[0]['payload'],{'model':'dola-seedream-5-0-flash-260915','prompt':'蓝色山谷','size':'1.5K',
            'response_format':'url','watermark':False,'output_format':'jpeg'})
        self.assertEqual(result,{'data':[{'url':'https://cdn.example.test/fixture.png'}]})

    def test_flash_real_edit_preserves_mixed_references_and_base64_result(self):
        ref=self.references(1)[0];inline='data:image/png;base64,iVBORw0KGgo=';remote='https://cdn.example.test/ref.png'
        client,calls=self.transport(result={'data':[{'b64_json':'iVBORw0KGgo='}]})
        req=self.request(model='seedream-5-flash',image=ref,reference_images=[remote,inline],response_format='b64_json')
        with patch('angemedia_gateway.providers.image.bytedance.provider_client',new=client):
            result=asyncio.run(ByteDanceImageProvider().generate(req,self.target('seedream-5-flash')))
        self.assertEqual(calls[0]['payload']['image'][1:],[remote,inline]);self.assertEqual(result,{'data':[{'b64_json':'iVBORw0KGgo='}]})
        self.assertTrue(calls[0]['payload']['image'][0].startswith('data:image/png;base64,'))

    def test_single_image_adapter_rejects_multiple_empty_and_malformed_outputs(self):
        for model in ('seedream-3','seedream-5-pro','seedream-5-flash','seedream-5-lite'):
            for data in ([],[{'url':'https://cdn.example.test/a.png'},{'url':'https://cdn.example.test/b.png'}],{},[None]):
                with self.subTest(model=model,data=data):
                    client,calls=self.transport(result={'data':data})
                    with patch('angemedia_gateway.providers.image.bytedance.provider_client',new=client),self.assertRaises(ProviderProtocolError):
                        asyncio.run(ByteDanceImageProvider().generate(ImageRequest(prompt='单图',model=model,size='2K'),self.target(model)))
                    self.assertEqual(len(calls),1)

    def test_undeclared_output_modes_are_rejected_before_jobs_or_provider_calls(self):
        from angemedia_gateway.services.image_execution import build_image_execution_plan
        before=self.counts()
        for field,value in (('layer_decomposition',True),('sequential_image_generation','auto'),('sequential_image_generation_options',{'max_images':3}),('stream',True)):
            with self.subTest(field=field):
                req=ImageRequest(prompt='需要特殊输出',model='seedream-5-flash',size='1.5K',**{field:value})
                with self.assertRaises(InvalidImageRequest):build_image_execution_plan(req)
        self.assertEqual(self.counts(),before)

    def test_directory_external_targets_keep_their_existing_validation_passthrough(self):
        req=ImageRequest(prompt='外部自定义协议',stream=True,layer_decomposition=True)
        validate_image_operation_request(req,[RouteTarget('bytedance','raw-endpoint-fixture')])
        validate_image_operation_request(req,[RouteTarget('custom:fixture','raw-endpoint-fixture')])

    def test_flash_agent_confirmation_and_original_worker_deliver_one_asset(self):
        refs=self.references(10)
        action=self.propose(request={'prompt':'保留十张图的一致性','model':'seedream-flash','size':'1.5K','operation':'edit','reference_images':refs})
        self.assertEqual(self.counts(),(0,0,0));self.assertTrue(action['review']['allowed'])
        self.assertIn('may_incur_cost',action['review']['required_acknowledgements'])
        self.assertFalse(action['review']['routes'][0]['generation_verified'])
        job=self.job(action);first=self.message(job['id']);client,calls=self.transport()
        runtime=self.image_runtime(providers={'bytedance':ByteDanceImageProvider()})
        with patch('angemedia_gateway.providers.image.bytedance.provider_client',new=client):
            self.assertEqual(runtime.handle(first)['status'],'succeeded')
            self.assertEqual(runtime.handle(first)['status'],'duplicate')
        self.assertEqual(len(calls),1);self.assertEqual(calls[0]['payload']['model'],'dola-seedream-5-0-flash-260915')
        self.assertEqual(calls[0]['payload']['size'],'1.5K');self.assertEqual(len(calls[0]['payload']['image']),10)
        self.assertEqual(len(list_assets(job_id=job['id'])),1);self.assertIsNone(current_execution_scope())
        self.flash_observation={'fixture_only':True,'production_database_read':False,'actual_provider_or_llm_calls':0,
            'model':'dola-seedream-5-0-flash-260915','jobs_before_confirmation':0,'reference_images':10,'size':'1.5K',
            'provider_posts':len(calls),'assets':len(list_assets(job_id=job['id'])),'terminal_replay':'duplicate',
            'requires_paid_acknowledgement':True,'generation_verified_for_live_account':False}
