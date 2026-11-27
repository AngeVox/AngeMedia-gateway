"""认证与五图编辑经原配置、适配器、动作worker和资产链路验收。"""
import asyncio
import base64
from contextlib import asynccontextmanager, closing
from datetime import date
import json
import unittest
from unittest.mock import patch

import httpx

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway import config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.error_diagnostics import classify_provider_error
from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.providers.catalog.api import catalog_api_response
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.providers.catalog.policy import policy_api_response
from angemedia_gateway.providers.catalog.validation import CatalogOperationValidationError
from angemedia_gateway.providers.errors import BackendUnavailable, ProviderError, ProviderProtocolError
from angemedia_gateway.providers.image.pollinations import PollinationsProvider
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.provider_runtime_config import clear_provider_runtime_api_key, update_provider_runtime_config
from angemedia_gateway.services.admin_service import AdminService
from angemedia_gateway.schemas import ImageRequest
from angemedia_gateway.services.image_execution import ImageExecutionPlan, ImageExecutionService, InvalidImageRequest
from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService
from angemedia_gateway.services.image_job_worker import ImageJobWorker
from angemedia_gateway.services.job_stage_registry import JobStageRegistry
from angemedia_gateway.services.worker_runtime import WorkerRuntime
from test_siliconflow_qwen_image_contract import PNG, REMOTE_URL
from test_web_studio_generate_image_operation_controls import run_operation_helper_script


KEY = 'fixture-pollinations-credential-123456789'


class PollinationsAuthenticatedContractTest(ActionWorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        key = patch.object(C, 'POLLINATIONS_API_KEY', ''); key.start(); self.addCleanup(key.stop)
        update_provider_runtime_config('pollinations', enabled=True, api_key=KEY, base_url_override='https://pollinations.example.test/v1')

    def refs(self, count):
        result = []
        for index in range(count):
            filename = f'pollinations-{index}.png'; (self.uploads/filename).write_bytes(PNG); result.append('/uploads/'+filename)
        return result

    def request(self, **values):
        return ImageRequest(prompt='保留人物，改成秋天的背景', model='pollinations-edit', **values)

    def clients(self, body=None, status=200):
        calls = []
        def reply(request):
            item = {'method': request.method, 'url': str(request.url), 'authorization': request.headers.get('Authorization')}
            if request.method == 'POST':
                item['json'] = json.loads(request.content); calls.append(item)
                return httpx.Response(status, json=body if body is not None else {'data': [{'b64_json': base64.b64encode(PNG).decode()}]})
            calls.append(item); return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        @asynccontextmanager
        async def provider(**kwargs):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client: yield client
        def download(): return httpx.AsyncClient(transport=httpx.MockTransport(reply))
        return provider, download, calls

    def runtime(self, providers=None):
        worker = ImageJobWorker(executor=ImageExecutionService(providers=providers or {'pollinations': PollinationsProvider()}))
        return WorkerRuntime(registry=JobStageRegistry({'image_generate': worker.handle}), runtime_refresher=lambda: None)

    def test_missing_key_blocks_generation_and_edit_before_network_or_reference_materialization(self):
        clear_provider_runtime_api_key('pollinations')
        requests = [(ImageRequest(prompt='小屋', model='pollinations'), 'zimage'), (self.request(image='/uploads/missing.png'), 'p-image-edit')]
        for req, model in requests:
            with self.subTest(model=model), patch('angemedia_gateway.providers.image.pollinations.provider_client') as client, patch('angemedia_gateway.providers.image.pollinations.prepare_builtin_reference') as reference, self.assertRaises(BackendUnavailable) as caught:
                asyncio.run(PollinationsProvider().generate(req, RouteTarget('pollinations', model)))
            client.assert_not_called(); reference.assert_not_called()
            self.assertEqual(caught.exception.error_category, 'auth'); self.assertFalse(classify_provider_error(str(caught.exception))['retryable'])
        self.assertEqual(list(self.output.iterdir()), []); self.assertEqual(PollinationsProvider().health(), 'not_configured')

    def test_admin_readiness_uses_effective_runtime_key_and_enable_state(self):
        service = AdminService(); self.assertTrue(service.builtin_configured('pollinations'))
        row = next(row for row in service.builtin_provider_rows() if row['id'] == 'pollinations')
        self.assertTrue(row['configured']); self.assertTrue(row['ready'])
        update_provider_runtime_config('pollinations', enabled=False)
        row = next(row for row in service.builtin_provider_rows() if row['id'] == 'pollinations')
        self.assertTrue(row['configured']); self.assertFalse(row['ready'])
        clear_provider_runtime_api_key('pollinations'); self.assertFalse(service.builtin_configured('pollinations'))
        self.assertNotIn(KEY, json.dumps(service.builtin_provider_rows()))

    def test_environment_key_fallback_matches_the_adapter_and_admin(self):
        clear_provider_runtime_api_key('pollinations')
        with patch.object(C, 'POLLINATIONS_API_KEY', KEY):
            self.assertTrue(AdminService().builtin_configured('pollinations')); self.assertEqual(PollinationsProvider().health(), 'configured_key')

    def test_catalog_requires_key_stays_disabled_by_default_and_preserves_aliases(self):
        catalog = load_provider_catalog(); provider = catalog.providers_by_id['pollinations']
        self.assertTrue(provider.requires_key); self.assertFalse(provider.enabled_default)
        self.assertNotIn('pollinations', [row.id for row in catalog.default_image_chain()])
        self.assertEqual(catalog.models_by_id['pollinations'].provider_model, 'zimage')
        self.assertEqual(catalog.models_by_id['pollinations-edit'].provider_model, 'p-image-edit')
        self.assertEqual(catalog.models_by_id['pollinations-edit'].operations['image_edit'].refs[0].max_total, 5)

    def test_public_policy_evidence_expires_without_claiming_account_rights(self):
        policy = load_provider_catalog().providers_by_id['pollinations'].policy
        current = policy_api_response(policy, as_of=date(2026, 10, 30)); expired = policy_api_response(policy, as_of=date(2026, 11, 6))
        self.assertEqual(current['billing'], 'usage_credits'); self.assertIn('credential', current['account_requirements'])
        self.assertFalse(current['account_access_verified']); self.assertFalse(current['generation_verified']); self.assertEqual(expired['review_status'], 'review_due')

    def test_five_total_images_are_sent_in_order_with_bearer_only_in_header(self):
        refs = self.refs(5); provider, _, calls = self.clients()
        req = self.request(image=refs[0], reference_images=refs[1:], quality='high')
        with patch('angemedia_gateway.providers.image.pollinations.provider_client', new=provider):
            asyncio.run(PollinationsProvider().generate(req, RouteTarget('pollinations', 'p-image-edit')))
        self.assertEqual(calls[0]['url'], 'https://pollinations.example.test/v1/images/edits')
        self.assertEqual(calls[0]['authorization'], 'Bearer '+KEY)
        payload = calls[0]['json']; self.assertEqual(len(payload['image']), 5); self.assertEqual(payload['quality'], 'high')
        self.assertTrue(all(row['image_url'] == 'data:image/png;base64,'+base64.b64encode(PNG).decode() for row in payload['image']))
        self.assertNotIn('response_format', payload); self.assertNotIn(KEY, calls[0]['url']); self.assertNotIn(KEY, json.dumps(payload))

    def test_sixth_image_and_mask_are_rejected_before_admission_and_http(self):
        refs = self.refs(6); provider, _, calls = self.clients()
        for req in (self.request(reference_images=refs), self.request(reference_images=refs[:5], mask=refs[5])):
            with self.subTest(request=req.model_dump()), self.assertRaises(InvalidImageRequest): ImageJobAdmissionService().prepare(req)
            with patch('angemedia_gateway.providers.image.pollinations.provider_client', new=provider), self.assertRaises(CatalogOperationValidationError):
                asyncio.run(PollinationsProvider().generate(req, RouteTarget('pollinations', 'p-image-edit')))
        self.assertEqual(calls, []); self.assertEqual(self.counts(), (0, 0, 0))

    def test_generation_uses_original_json_endpoint_and_return_format(self):
        provider, _, calls = self.clients()
        req = ImageRequest(prompt='山谷小屋', model='pollinations', size='1024x1024', response_format='b64_json')
        with patch('angemedia_gateway.providers.image.pollinations.provider_client', new=provider):
            asyncio.run(PollinationsProvider().generate(req, RouteTarget('pollinations', 'zimage')))
        self.assertEqual(calls[0]['json'], {'prompt': req.prompt, 'model': 'zimage', 'n': 1, 'size': '1024x1024', 'response_format': 'b64_json'})
        self.assertEqual(calls[0]['url'], 'https://pollinations.example.test/v1/images/generations')

    def test_url_response_can_use_original_base64_normalizer_without_forwarding_key(self):
        provider, download, calls = self.clients({'data': [{'url': REMOTE_URL}]})
        req = ImageRequest(prompt='小屋', model='pollinations', response_format='b64_json')
        plan = ImageExecutionPlan(mode='builtin', routes=(('pollinations', 'zimage'),))
        with patch('angemedia_gateway.providers.image.pollinations.provider_client', new=provider), patch('angemedia_gateway.media._remote_media_http_client', new=download):
            result = asyncio.run(ImageExecutionService(providers={'pollinations': PollinationsProvider()}).execute(req, plan))
        self.assertEqual(base64.b64decode(result.result['data'][0]['b64_json']), PNG)
        self.assertEqual([row['method'] for row in calls], ['POST', 'GET']); self.assertIsNone(calls[1]['authorization'])

    def test_malformed_json_result_shapes_do_not_import_assets(self):
        bodies = ({}, {'data': 'bad'}, {'data': [None]}, {'data': [{}]}, {'data': [{'url': 1}]}, {'data': [{'b64_json': []}]}, {'data': [{'url': 'file:///private.png'}]}, {'data': [{'b64_json': ' '}]})
        for body in bodies:
            provider, _, calls = self.clients(body)
            with self.subTest(body=body), patch('angemedia_gateway.providers.image.pollinations.provider_client', new=provider), self.assertRaises(ProviderProtocolError):
                asyncio.run(PollinationsProvider().generate(ImageRequest(prompt='小屋', model='pollinations'), RouteTarget('pollinations', 'zimage')))
            self.assertEqual(len(calls), 1)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_401_402_403_429_and_5xx_never_fall_back_to_public_generation(self):
        for status in (401, 402, 403, 429, 500):
            provider, _, calls = self.clients({'error': {'message': 'private-upstream-'+KEY}}, status=status)
            with self.subTest(status=status), patch('angemedia_gateway.providers.image.pollinations.provider_client', new=provider), self.assertRaises(ProviderError) as caught:
                asyncio.run(PollinationsProvider().generate(ImageRequest(prompt='小屋', model='pollinations'), RouteTarget('pollinations', 'zimage')))
            self.assertEqual(caught.exception.status_code, status); self.assertNotIn(KEY, str(caught.exception)); self.assertEqual(len(calls), 1)

    def test_missing_key_blocks_action_confirmation_without_creating_a_job(self):
        clear_provider_runtime_api_key('pollinations')
        action = self.propose(request={'prompt': '小屋', 'model': 'pollinations'})
        self.assertFalse(action['review']['allowed']); self.assertIn('provider_credentials_missing', action['review']['blocked_reasons'])
        self.assert_code('action_policy_denied', self.confirm, action); self.assertEqual(self.counts(), (0, 0, 0))

    def test_confirmed_five_image_edit_delivers_one_asset_and_terminal_replay_is_http_free(self):
        refs = self.refs(5); action = self.propose(request=self.request(reference_images=refs, quality='high').model_dump(exclude_none=True))
        self.assertEqual(self.counts(), (0, 0, 0)); self.assertIn('may_incur_cost', action['review']['required_acknowledgements'])
        job = self.job(action); message = self.message(job['id']); provider, _, calls = self.clients()
        with patch('angemedia_gateway.providers.image.pollinations.provider_client', new=provider):
            runtime = self.runtime(); self.assertEqual(runtime.handle(message)['status'], 'succeeded'); self.assertEqual(runtime.handle(message)['status'], 'duplicate')
        self.assertEqual(len(calls), 1); self.assertEqual(len(calls[0]['json']['image']), 5)
        assets = list_assets(job_id=job['id']); self.assertEqual(len(assets), 1); self.assertEqual((self.output/assets[0]['relative_path']).read_bytes(), PNG)
        self.observation = {'fixture_only': True, 'actual_generation_or_llm_calls': 0, 'production_database_read': False, 'jobs_before_confirmation': 0,
            'input_images': 5, 'provider_posts': 1, 'assets': 1, 'replay': 'duplicate', 'bearer_header_only': True,
            'legacy_public_generation_used': False, 'original_confirmation_worker_localizer_assets': True}

    def test_key_change_before_execution_is_rejected_without_request(self):
        job = self.job(self.propose(request={'prompt': '小屋', 'model': 'pollinations'}))
        update_provider_runtime_config('pollinations', api_key='fixture-rotated-pollinations-key')
        provider, _, calls = self.clients()
        with patch('angemedia_gateway.providers.image.pollinations.provider_client', new=provider): self.assert_rejected(self.runtime(), job)
        self.assertEqual(calls, [])

    def test_original_key_and_references_remain_frozen_after_call_begins(self):
        refs = self.refs(5); job = self.job(self.propose(request=self.request(reference_images=refs).model_dump(exclude_none=True)))
        provider, _, calls = self.clients()
        class ChangeAfterStart:
            async def generate(inner, request, target):
                for ref in refs: (self.uploads/ref.rsplit('/', 1)[1]).write_bytes(PNG+b'changed')
                update_provider_runtime_config('pollinations', api_key='fixture-rotated-pollinations-key', base_url_override='https://changed.invalid/v1')
                return await PollinationsProvider().generate(request, target)
        with patch('angemedia_gateway.providers.image.pollinations.provider_client', new=provider):
            self.assertEqual(self.runtime({'pollinations': ChangeAfterStart()}).handle(self.message(job['id']))['status'], 'succeeded')
        self.assertEqual(calls[0]['authorization'], 'Bearer '+KEY); self.assertEqual(calls[0]['url'], 'https://pollinations.example.test/v1/images/edits')
        self.assertTrue(all(row['image_url'] == 'data:image/png;base64,'+base64.b64encode(PNG).decode() for row in calls[0]['json']['image']))

    def test_catalog_drives_five_image_ui_payload_without_new_frontend_fields(self):
        model = next(row for row in catalog_api_response(load_provider_catalog())['models'] if row['id'] == 'pollinations-edit')
        script = """
        import assert from 'node:assert/strict';import fs from 'node:fs';
        import {imageReferenceSpecs} from './operation-capabilities.js';import {buildOperationPayload} from './operation-payload.js';
        const model=JSON.parse(fs.readFileSync(0,'utf8'));const images=Array.from({length:5},(_,i)=>'/uploads/'+i+'.png');
        assert.equal(imageReferenceSpecs(model,'image_edit')[0].max_count,5);
        assert.deepEqual(buildOperationPayload(model,{operation:'edit',reference_images:images}),{operation:'edit',reference_images:images});
        console.log(JSON.stringify({ok:true}));
        """
        self.assertTrue(run_operation_helper_script(script, model)['ok'])
