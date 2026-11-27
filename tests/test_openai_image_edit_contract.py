"""OpenAI编辑从目录到真实multipart与原动作worker闭环；只替换远端HTTP。"""
import asyncio
import base64
from contextlib import asynccontextmanager, closing
from email import policy
from email.parser import BytesParser
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from assistant_action_worker_support import ActionWorkerFixture
import angemedia_gateway.config as C
from angemedia_gateway.assistant_action_contract import ActionError
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.media_execution_scope import current_execution_scope
from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.providers.catalog.validation import CatalogOperationValidationError, validate_image_operation_request, validate_operation_refs
from angemedia_gateway.providers.custom_capabilities import validate_custom_image_request
from angemedia_gateway.providers.errors import BackendUnavailable
from angemedia_gateway.providers.image.openai_compatible import OpenAICompatibleImageProvider
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.schemas import ImageRequest
from angemedia_gateway.services.assistant_action_references import inspect_references
from angemedia_gateway.services.assistant_action_specs import ImageProposalArgs
from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4//8/AwAI/AL+p5qgoAAAAABJRU5ErkJggg==')


class OpenAIImageEditContractTest(ActionWorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        credential=patch.object(C,'OPENAI_IMAGE_API_KEY','fixture-openai-image-credential-123456789')
        credential.start();self.addCleanup(credential.stop)
        update_provider_runtime_config('openai_image',enabled=True,base_url_override='https://openai.example.test/v1')

    def references(self,count):
        refs=[]
        for index in range(count):
            name=f'openai-{index}.png';(self.uploads/name).write_bytes(PNG);refs.append('/uploads/'+name)
        return refs

    def request(self, *, model='openai-flare', **values):
        return ImageRequest(prompt='保留人物，替换背景',model=model,operation='edit',**values)

    def target(self,model='openai-flare'):
        value=load_provider_catalog().models_by_id[model]
        return RouteTarget(value.provider,value.provider_model)

    def transport(self):
        calls=[]
        def reply(request):
            value={'url':str(request.url),'authorization':request.headers['Authorization']}
            if request.headers['content-type'].startswith('multipart/form-data'):
                message=BytesParser(policy=policy.default).parsebytes(b'Content-Type: '+request.headers['content-type'].encode()+b'\r\nMIME-Version: 1.0\r\n\r\n'+request.content)
                value['parts']=[{'name':part.get_param('name',header='content-disposition'),'filename':part.get_filename(),
                                 'content':part.get_payload(decode=True)} for part in message.iter_parts()]
            else:value['json']=json.loads(request.content)
            calls.append(value)
            return httpx.Response(200,json={'created':0,'data':[{'b64_json':base64.b64encode(PNG).decode()}]})
        @asynccontextmanager
        async def client(**kwargs):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as result:yield result
        return client,calls

    def test_flare_keeps_independent_identity_and_shares_current_sunburst_edit_contract(self):
        catalog=load_provider_catalog();flare=catalog.models_by_id['openai-flare'];sunburst=catalog.models_by_id['openai-image']
        self.assertTrue(flare.capabilities['image_edit']);self.assertEqual(flare.operations,sunburst.operations)
        self.assertIsNot(flare.operations,sunburst.operations)
        self.assertEqual(flare.provider_model,'gpt-image-2.5-flare');self.assertEqual(flare.tags,('explicit','fast'))
        self.assertIsNone(flare.default_chain_order);self.assertTrue(catalog.providers_by_id['openai_image'].enabled_default)
        self.assertNotIn(flare.id,[model.id for model in catalog.default_image_chain()])

    def test_all_supported_openai_models_accept_sixteen_references_and_separate_mask(self):
        refs=self.references(17)
        for model in ('openai-image','openai-flare','openai-image-2'):
            with self.subTest(model=model):
                req=self.request(model=model,reference_images=refs[:16],mask=refs[16])
                validate_image_operation_request(req,[self.target(model)])
                self.assertEqual(len(ImageProposalArgs.model_validate({'request':req.model_dump()}).request.reference_images),16)

    def test_reference_limit_counts_single_image_and_list_but_not_mask(self):
        refs=self.references(18)
        valid=self.request(image=refs[0],reference_images=refs[1:16],mask=refs[17])
        validate_image_operation_request(valid,[self.target()])
        with self.assertRaises(CatalogOperationValidationError):
            validate_image_operation_request(valid.model_copy(update={'reference_images':refs[1:17]}),[self.target()])
        with self.assertRaises(ValidationError):self.request(reference_images=refs[:17])
        self.assertEqual(self.counts(),(0,0,0))

    def test_other_models_and_custom_providers_keep_their_original_reference_limits(self):
        refs=self.references(16);catalog=load_provider_catalog()
        for model in ('seedream-5-pro','seedream-5-flash','seedream-5-lite','qwen-edit-2511'):
            with self.subTest(model=model),self.assertRaises(CatalogOperationValidationError):
                validate_operation_refs(self.request(model=model,reference_images=refs),catalog.models_by_id[model],'image_edit')
        with self.assertRaises(ValueError):
            validate_custom_image_request(self.request(reference_images=refs),{'capabilities':{'image_edit':True,'max_reference_images':10,'supports_mask':True}})

    def test_real_flare_adapter_sends_sixteen_ordered_files_and_mask_without_mutating_request(self):
        refs=self.references(17);req=self.request(image=refs[0],reference_images=refs[1:16],mask=refs[16],quality='max')
        before=req.model_dump();client,calls=self.transport()
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client',new=client):
            result=asyncio.run(OpenAICompatibleImageProvider().generate(req,self.target()))
        self.assertEqual(req.model_dump(),before);self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['url'],'https://openai.example.test/v1/images/edits')
        parts=calls[0]['parts'];files=[p for p in parts if p['filename']];fields={p['name']:p['content'].decode() for p in parts if not p['filename']}
        self.assertEqual([p['name'] for p in files],['image[]']*16+['mask'])
        self.assertEqual([p['filename'] for p in files],[f'image-{i}.png' for i in range(1,17)]+['mask-1.png'])
        self.assertTrue(all(p['content']==PNG for p in files))
        self.assertEqual(fields,{'model':'gpt-image-2.5-flare','prompt':req.prompt,'n':'1','size':'1024x1024','quality':'max'})
        self.assertEqual(base64.b64decode(result['data'][0]['b64_json']),PNG)

    def test_flare_plain_generation_still_uses_existing_json_endpoint(self):
        client,calls=self.transport()
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client',new=client):
            result=asyncio.run(OpenAICompatibleImageProvider().generate(ImageRequest(prompt='山谷',model='openai-flare',quality='high'),self.target()))
        self.assertEqual(calls[0]['url'],'https://openai.example.test/v1/images/generations')
        self.assertEqual(calls[0]['json']['n'],1);self.assertEqual(calls[0]['json']['model'],'gpt-image-2.5-flare')
        self.assertEqual(len(result['data']),1)

    def test_quality_levels_remain_model_specific(self):
        ref=self.references(1)[0]
        for model in ('openai-image','openai-flare'):
            for quality in ('xhigh','max'):
                with self.subTest(model=model,quality=quality):
                    validate_image_operation_request(self.request(model=model,image=ref,quality=quality),[self.target(model)])
        validate_image_operation_request(self.request(model='openai-image-2',image=ref,quality='high'),[self.target('openai-image-2')])
        with self.assertRaises(CatalogOperationValidationError):
            validate_image_operation_request(self.request(model='openai-image-2',image=ref,quality='max'),[self.target('openai-image-2')])

    def test_bad_files_remote_urls_and_empty_edit_stop_before_provider_http(self):
        client,calls=self.transport();ref=self.references(1)[0]
        requests=[self.request(),self.request(image='/uploads/missing.png'),self.request(image='/uploads/../openai-0.png'),
                  self.request(image='https://cdn.example.test/not-gateway.png'),self.request(image=ref,mask='http://127.0.0.1/private.png')]
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client',new=client):
            for request in requests:
                with self.subTest(request=request.model_dump()),self.assertRaises((BackendUnavailable,ValueError)):
                    asyncio.run(OpenAICompatibleImageProvider().generate(request,self.target()))
        self.assertEqual(calls,[])

    def test_durable_image_jobs_do_not_persist_inline_binary_or_external_urls(self):
        for reference in ('data:image/png;base64,'+base64.b64encode(PNG).decode(),'https://cdn.example.test/ref.png'):
            with self.subTest(reference=reference),self.assertRaises(ValueError):
                ImageJobAdmissionService().prepare(self.request(image=reference))
        self.assertEqual(self.counts(),(0,0,0))

    def test_agent_sixteen_image_and_mask_confirmation_delivers_one_asset_once(self):
        refs=self.references(17)
        action=self.propose(request=self.request(reference_images=refs[:16],mask=refs[16],quality='max').model_dump(exclude_none=True))
        self.assertEqual(self.counts(),(0,0,0));self.assertTrue(action['review']['allowed'])
        self.assertIn('may_incur_cost',action['review']['required_acknowledgements'])
        job=self.job(action);first=self.message(job['id']);client,calls=self.transport()
        runtime=self.image_runtime(providers={'openai_image':OpenAICompatibleImageProvider()})
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client',new=client):
            self.assertEqual(runtime.handle(first)['status'],'succeeded');self.assertEqual(runtime.handle(first)['status'],'duplicate')
        self.assertEqual(len(calls),1);self.assertEqual(len([p for p in calls[0]['parts'] if p['filename']]),17)
        self.assertEqual(len(list_assets(job_id=job['id'])),1);self.assertIsNone(current_execution_scope())
        with closing(db_connect()) as conn:
            evidence='\n'.join(str(dict(row)) for table in ('jobs','job_events','assistant_actions','assistant_action_execution_grants','job_attempts') for row in conn.execute('SELECT * FROM '+table))
        self.assertNotIn('data:image/',evidence);self.assertNotIn('fixture-openai-image-credential',evidence)
        self.observation={'fixture_only':True,'actual_provider_or_llm_calls':0,'production_database_read':False,
            'jobs_before_confirmation':0,'reference_images':16,'mask_files':1,'provider_posts':1,'assets':1,
            'terminal_replay':'duplicate','source_binary_persisted':False,'requires_paid_acknowledgement':True}

    def test_changed_mask_before_worker_rejects_without_provider_call(self):
        refs=self.references(17);job=self.job(self.propose(request=self.request(reference_images=refs[:16],mask=refs[16]).model_dump(exclude_none=True)))
        (self.uploads/'openai-16.png').write_bytes(PNG+b'changed');client,calls=self.transport()
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client',new=client):
            self.assert_rejected(self.image_runtime(providers={'openai_image':OpenAICompatibleImageProvider()}),job)
        self.assertEqual(calls,[])

    def test_original_files_mask_and_account_are_frozen_after_call_starts(self):
        refs=self.references(17);job=self.job(self.propose(request=self.request(reference_images=refs[:16],mask=refs[16]).model_dump(exclude_none=True)))
        client,calls=self.transport();scopes=[]
        async def generate(request,target):
            scopes.append(current_execution_scope())
            for ref in refs:(self.uploads/ref.rsplit('/',1)[1]).write_bytes(PNG+b'changed')
            update_provider_runtime_config('openai_image',api_key='fixture-rotated-key',base_url_override='https://changed.invalid/v1')
            return await OpenAICompatibleImageProvider().generate(request,target)
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client',new=client):
            self.assertEqual(self.image_runtime(providers={'openai_image':SimpleNamespace(generate=generate)}).handle(self.message(job['id']))['status'],'succeeded')
        self.assertEqual(calls[0]['authorization'],'Bearer fixture-openai-image-credential-123456789')
        self.assertEqual(calls[0]['url'],'https://openai.example.test/v1/images/edits')
        self.assertTrue(all(p['content']==PNG for p in calls[0]['parts'] if p['filename']))
        self.assertTrue(scopes[0]._closed);self.assertEqual(scopes[0]._references,{})

    def test_changed_account_before_worker_never_submits_to_new_account(self):
        refs=self.references(1);job=self.job(self.propose(request=self.request(image=refs[0]).model_dump(exclude_none=True)))
        update_provider_runtime_config('openai_image',api_key='fixture-rotated-key');client,calls=self.transport()
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client',new=client):
            self.assert_rejected(self.image_runtime(providers={'openai_image':OpenAICompatibleImageProvider()}),job)
        self.assertEqual(calls,[])

    def test_action_observation_has_capacity_for_mask_and_rejects_larger_envelope(self):
        refs=self.references(18)
        observed,_=inspect_references({'reference_images':refs[:16],'mask':refs[16]})
        self.assertEqual(len(observed),17)
        with self.assertRaises(ActionError) as caught:inspect_references({'reference_images':refs[:17],'mask':refs[17]})
        self.assertEqual(caught.exception.code,'action_reference_limit')
