"""撤销字段与未知型号边界贯通原API校验、适配器和人工确认执行。"""
import asyncio
from contextlib import asynccontextmanager, closing
from dataclasses import replace
from datetime import date
import json
import unittest
from unittest.mock import patch

import httpx

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.providers.catalog.policy import policy_api_response
from angemedia_gateway.providers.catalog.validation import CatalogOperationValidationError, validate_image_operation_request
from angemedia_gateway.providers.image.siliconflow import SiliconFlowProvider, build_siliconflow_image_payload
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.schemas import ImageRequest
from angemedia_gateway.services.image_execution import ImageExecutionService, InvalidImageRequest
from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService
from angemedia_gateway.services.image_job_worker import ImageJobWorker
from angemedia_gateway.services.job_stage_registry import JobStageRegistry
from angemedia_gateway.services.worker_runtime import WorkerRuntime
from test_siliconflow_qwen_image_contract import PNG, REMOTE_URL


class SiliconFlowRequestBoundaryTest(ActionWorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        update_provider_runtime_config('siliconflow', enabled=True, base_url_override='https://siliconflow.example.test/v1')

    def request(self, **values):
        return ImageRequest(prompt='山谷里的小屋', model='siliconflow::ExampleOrg/Unverified-Image', **values)

    def target(self):
        return RouteTarget('siliconflow', 'ExampleOrg/Unverified-Image')

    def clients(self):
        calls = []
        def reply(request):
            row = {'method': request.method, 'url': str(request.url), 'authorization': request.headers.get('Authorization'),
                   'watermark': request.headers.get('X-Enable-Watermark')}
            if request.method == 'POST':
                row['json'] = json.loads(request.content); calls.append(row)
                return httpx.Response(200, json={'images': [{'url': REMOTE_URL}]})
            calls.append(row); return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        @asynccontextmanager
        async def provider(**kwargs):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client: yield client
        def download(): return httpx.AsyncClient(transport=httpx.MockTransport(reply))
        return provider, download, calls

    def runtime(self):
        worker = ImageJobWorker(executor=ImageExecutionService(providers={'siliconflow': SiliconFlowProvider()}))
        return WorkerRuntime(registry=JobStageRegistry({'image_generate': worker.handle}), runtime_refresher=lambda: None)

    def test_retired_batch_size_rejects_all_models_before_admission(self):
        for model in ('kolors', 'siliconflow-qwen-image', 'siliconflow-qwen-edit-2509', 'siliconflow::ExampleOrg/Unverified-Image'):
            for options in ({'batch_size': 1}, {'batch_size': None}, {'extra_body': {'batch_size': 1}}):
                with self.subTest(model=model, options=options), self.assertRaises((InvalidImageRequest, ValueError)):
                    ImageJobAdmissionService().prepare(ImageRequest(prompt='小屋', model=model, **options))
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_direct_adapter_rejects_retired_field_before_http_client_opens(self):
        for upstream in ('Kwai-Kolors/Kolors', 'Qwen/Qwen-Image', 'Qwen/Qwen-Image-Edit-2509', 'ExampleOrg/Unverified-Image'):
            with self.subTest(upstream=upstream), patch('angemedia_gateway.providers.image.siliconflow.provider_client') as client, self.assertRaises(CatalogOperationValidationError):
                asyncio.run(SiliconFlowProvider().generate(self.request(batch_size=1), RouteTarget('siliconflow', upstream)))
            client.assert_not_called()

    def test_kolors_json_omits_retired_field_and_keeps_current_params(self):
        provider, _, calls = self.clients()
        req = ImageRequest(prompt='小屋', model='kolors', size='960x1280', guidance=8.5, seed=0, steps=30)
        with patch('angemedia_gateway.providers.image.siliconflow.provider_client', new=provider):
            asyncio.run(SiliconFlowProvider().generate(req, RouteTarget('siliconflow', 'Kwai-Kolors/Kolors')))
        self.assertEqual(calls[0]['json'], {'model': 'Kwai-Kolors/Kolors', 'prompt': '小屋', 'image_size': '960x1280', 'guidance_scale': 8.5, 'seed': 0, 'num_inference_steps': 30})
        self.assertIsNone(calls[0]['watermark'])

    def test_kolors_uses_its_catalog_defaults_and_never_the_old_size_constant(self):
        catalog = load_provider_catalog(); models = []
        for model in catalog.models:
            if model.id == 'kolors':
                operation = model.operations['text_to_image']; params = dict(operation.params)
                params['steps'] = replace(params['steps'], default=23); params['guidance'] = replace(params['guidance'], default=6.0)
                model = replace(model, operations={**model.operations, 'text_to_image': replace(operation, params=params)})
            models.append(model)
        with patch('angemedia_gateway.providers.image.siliconflow.load_provider_catalog', return_value=replace(catalog, models=tuple(models))), patch('angemedia_gateway.config.KOLORS_SIZES', {'1024x1024'}):
            payload = build_siliconflow_image_payload(ImageRequest(prompt='小屋', model='kolors', size='960x1280'), RouteTarget('siliconflow', 'Kwai-Kolors/Kolors'))
        self.assertEqual((payload['image_size'], payload['num_inference_steps'], payload['guidance_scale']), ('960x1280', 23, 6.0))

    def test_kolors_input_alias_uses_original_reference_delivery(self):
        import base64
        inline = 'data:image/png;base64,' + base64.b64encode(PNG).decode()
        for field in ('image', 'input_image'):
            with self.subTest(field=field):
                payload = build_siliconflow_image_payload(ImageRequest(prompt='小屋', model='kolors', **{field: inline}), RouteTarget('siliconflow', 'Kwai-Kolors/Kolors'))
                self.assertEqual(payload['image'], inline); self.assertNotIn('batch_size', payload)

    def test_unknown_model_sends_minimum_json_without_invented_defaults_or_size_coercion(self):
        provider, _, calls = self.clients()
        with patch('angemedia_gateway.providers.image.siliconflow.provider_client', new=provider):
            asyncio.run(SiliconFlowProvider().generate(self.request(size='1600x1000'), self.target()))
        self.assertEqual(calls[0]['json'], {'model': 'ExampleOrg/Unverified-Image', 'prompt': '山谷里的小屋', 'image_size': '1600x1000'})
        self.assertIsNone(calls[0]['watermark'])

    def test_unknown_model_preserves_only_explicit_common_parameters_including_zero_seed(self):
        payload = build_siliconflow_image_payload(self.request(size='1664x928', steps=9, seed=0, negative_prompt='模糊'), self.target())
        self.assertEqual(payload, {'model': 'ExampleOrg/Unverified-Image', 'prompt': '山谷里的小屋', 'image_size': '1664x928', 'num_inference_steps': 9, 'seed': 0, 'negative_prompt': '模糊'})

    def test_unknown_model_cannot_guess_guidance_edit_references_or_output_controls(self):
        (self.uploads/'a.png').write_bytes(PNG)
        options = ({'guidance': 4}, {'image': '/uploads/a.png'}, {'reference_images': ['/uploads/a.png']}, {'mask': '/uploads/a.png'},
                   {'operation': 'edit'}, {'output_format': 'png'}, {'quality': 'high'}, {'watermark': False}, {'safe': False}, {'user': 'demo'})
        for values in options:
            with self.subTest(values=values), self.assertRaises(InvalidImageRequest):
                ImageJobAdmissionService().prepare(self.request(**values))
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_unknown_direct_extra_fields_cannot_be_silently_ignored(self):
        for values in ({'cfg': 4}, {'guidance_scale': 7.5}, {'extra_body': {'seed': 2}}, {'unreviewed_field': 'private-test-value'}):
            with self.subTest(values=values), patch('angemedia_gateway.providers.image.siliconflow.provider_client') as client, self.assertRaises(CatalogOperationValidationError) as caught:
                asyncio.run(SiliconFlowProvider().generate(self.request(**values), self.target()))
            client.assert_not_called(); self.assertNotIn('private-test-value', str(caught.exception))

    def test_mapping_and_model_request_validation_have_same_unknown_contract(self):
        req = self.request(size='1600x1000', steps=9, seed=0)
        validate_image_operation_request(req, [self.target()])
        validate_image_operation_request(req.model_dump(), [self.target()])
        with self.assertRaises(CatalogOperationValidationError):
            validate_image_operation_request({**req.model_dump(), 'cfg': 4}, [self.target()])

    def test_unknown_or_known_invalid_size_is_rejected_instead_of_changed(self):
        for model, size in (('siliconflow::ExampleOrg/Unverified-Image', 'auto'), ('siliconflow::ExampleOrg/Unverified-Image', '0x1000'), ('kolors', '1600x1000')):
            with self.subTest(model=model, size=size), self.assertRaises(InvalidImageRequest):
                ImageJobAdmissionService().prepare(ImageRequest(prompt='小屋', model=model, size=size))

    def test_policy_is_public_evidence_with_review_expiry_and_no_account_guarantee(self):
        policy = load_provider_catalog().providers_by_id['siliconflow'].policy
        current = policy_api_response(policy, as_of=date(2026, 10, 30)); expired = policy_api_response(policy, as_of=date(2026, 11, 6))
        self.assertEqual(current['review_status'], 'current'); self.assertEqual(expired['review_status'], 'review_due')
        self.assertEqual(current['billing'], 'unknown'); self.assertFalse(current['account_access_verified']); self.assertFalse(current['generation_verified'])
        self.assertIn('real_name_verified', current['account_requirements'])
        self.assertIn('batch_size', current['notes'])

    def test_other_provider_raw_routes_keep_their_existing_validation_contract(self):
        req = ImageRequest(prompt='小屋', model='agnes_image::ExampleOrg/Unknown', image='https://example.test/ref.png', guidance=4)
        validate_image_operation_request(req, [RouteTarget('agnes_image', 'ExampleOrg/Unknown')])

    def test_unknown_action_requires_both_cost_and_unverified_model_acknowledgements(self):
        action = self.propose(request=self.request().model_dump(exclude_none=True))
        self.assertEqual(set(action['review']['required_acknowledgements']), {'may_incur_cost', 'unverified_model'})
        self.assert_code('action_acknowledgements_required', self.service.confirm, action['id'], self.confirmation(action, acknowledgements=['may_incur_cost']), access=self.alice)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_confirmed_unknown_model_delivers_one_asset_without_hidden_parameters_or_replay(self):
        action = self.propose(request=self.request(size='1600x1000', steps=9, seed=0).model_dump(exclude_none=True))
        self.assertEqual(self.counts(), (0, 0, 0)); job = self.job(action)
        provider, download, calls = self.clients(); message = self.message(job['id']); runtime = self.runtime()
        with patch('angemedia_gateway.providers.image.siliconflow.provider_client', new=provider), patch('angemedia_gateway.media._remote_media_http_client', new=download):
            self.assertEqual(runtime.handle(message)['status'], 'succeeded'); self.assertEqual(runtime.handle(message)['status'], 'duplicate')
        self.assertEqual([row['method'] for row in calls], ['POST', 'GET'])
        self.assertEqual(calls[0]['json'], {'model': 'ExampleOrg/Unverified-Image', 'prompt': '山谷里的小屋', 'image_size': '1600x1000', 'num_inference_steps': 9, 'seed': 0})
        self.assertIsNone(calls[1]['authorization']); assets = list_assets(job_id=job['id']); self.assertEqual(len(assets), 1)
        self.assertEqual((self.output/assets[0]['relative_path']).read_bytes(), PNG)
        self.observation = {'fixture_only': True, 'actual_provider_or_llm_calls': 0, 'production_database_read': False,
            'jobs_before_confirmation': 0, 'model_unverified': True, 'required_acknowledgements': ['may_incur_cost', 'unverified_model'],
            'provider_posts': 1, 'safe_downloads': 1, 'assets': 1, 'replay': 'duplicate', 'retired_field_sent': False,
            'invented_guidance_or_steps_defaults': False, 'original_size': '1600x1000', 'original_worker_localizer_assets': True}

    def test_unknown_confirmed_request_tampering_stops_before_http(self):
        job = self.job(self.propose(request=self.request(size='1600x1000').model_dump(exclude_none=True)))
        with closing(db_connect()) as conn:
            payload = json.loads(job['input_json']); payload['request']['guidance'] = 4
            conn.execute('UPDATE jobs SET input_json=? WHERE id=?', (json.dumps(payload), job['id'])); conn.commit()
        provider, _, calls = self.clients()
        with patch('angemedia_gateway.providers.image.siliconflow.provider_client', new=provider):
            self.assert_rejected(self.runtime(), get_job(job['id']))
        self.assertEqual(calls, [])
