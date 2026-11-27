"""SiliconFlow Qwen生成贯通目录、原适配器、确认授权及安全下载作品链路。"""
import asyncio
import base64
from contextlib import asynccontextmanager, closing
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import httpx

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway import config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.model_routes import catalog_image_aliases, resolve_image_target
from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.providers.catalog.api import catalog_api_response
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.providers.errors import ProviderError, ProviderProtocolError
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
from test_web_studio_generate_image_operation_controls import run_operation_helper_script


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4//8/AwAI/AL+p5qgoAAAAABJRU5ErkJggg==')
REMOTE_URL = 'https://93.184.216.34/qwen.png?fixture-signature=test'


class SiliconFlowQwenImageContractTest(ActionWorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        update_provider_runtime_config('siliconflow', enabled=True, base_url_override='https://siliconflow.example.test/v1')

    def request(self, **values):
        return ImageRequest(prompt='有中文招牌的山谷咖啡馆', model='siliconflow-qwen-image', **values)

    def target(self):
        return RouteTarget('siliconflow', 'Qwen/Qwen-Image')

    def clients(self, body=None, *, status=200, redirect=None):
        calls = []
        def reply(request):
            call = {'method': request.method, 'url': str(request.url), 'authorization': request.headers.get('Authorization'),
                    'watermark_header': request.headers.get('X-Enable-Watermark')}
            if request.method == 'POST':
                call['json'] = json.loads(request.content); calls.append(call)
                return httpx.Response(status, json=body if body is not None else {'images': [{'url': REMOTE_URL}]})
            calls.append(call)
            if redirect:
                return httpx.Response(302, headers={'location': redirect})
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        @asynccontextmanager
        async def provider_client(**kwargs):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
                yield client
        def download_client():
            return httpx.AsyncClient(transport=httpx.MockTransport(reply))
        return provider_client, download_client, calls

    def runtime(self, providers=None):
        worker = ImageJobWorker(executor=ImageExecutionService(providers=providers or {'siliconflow': SiliconFlowProvider()}))
        return WorkerRuntime(registry=JobStageRegistry({'image_generate': worker.handle}), runtime_refresher=lambda: None)

    def test_explicit_identity_aliases_do_not_change_existing_qwen_or_default_chain(self):
        catalog = load_provider_catalog(); model = catalog.models_by_id['siliconflow-qwen-image']
        for selector in ('siliconflow-qwen-image', 'qwen-sf-image', 'Qwen/Qwen-Image', 'siliconflow::Qwen/Qwen-Image'):
            with self.subTest(selector=selector):
                target = resolve_image_target(selector, catalog_image_aliases())
                self.assertEqual((target.provider, target.model), ('siliconflow', 'Qwen/Qwen-Image'))
        self.assertEqual(resolve_image_target('qwen', catalog_image_aliases()).provider, 'modelscope')
        self.assertNotIn(model.id, [value.id for value in catalog.default_image_chain()])
        self.assertEqual(model.status, 'experimental'); self.assertIsNone(model.default_chain_order)

    def test_catalog_owns_cfg_mapping_defaults_and_recommended_sizes(self):
        model = load_provider_catalog().models_by_id['siliconflow-qwen-image']; params = model.operations['text_to_image'].params
        self.assertEqual(set(model.operations), {'text_to_image'})
        self.assertEqual(params['guidance'].provider_field, 'cfg')
        self.assertEqual((params['guidance'].default, params['guidance'].min, params['guidance'].max), (4.0, 0.1, 20))
        self.assertEqual((params['steps'].default, params['steps'].min, params['steps'].max), (50, 1, 100))
        self.assertEqual(model.size.mode, 'freeform')
        self.assertIn('1472x1140', model.size_presets); self.assertIn('1140x1472', model.size_presets)
        self.assertEqual(model.operations['text_to_image'].refs, ())

    def test_every_recommended_and_custom_size_is_preserved_without_kolors_coercion(self):
        sizes = load_provider_catalog().models_by_id['siliconflow-qwen-image'].size_presets + ('1024x1024', '1600x1000', '1472x1104')
        for size in sizes:
            with self.subTest(size=size):
                req = self.request(size=size)
                ImageJobAdmissionService().prepare(req)
                payload = build_siliconflow_image_payload(req, self.target())
                self.assertEqual(payload['image_size'], size)
                self.assertEqual((payload['num_inference_steps'], payload['cfg']), (50, 4.0))
                self.assertTrue({'guidance_scale', 'batch_size', 'image', 'response_format'}.isdisjoint(payload))

    def test_adapter_takes_defaults_from_catalog_instead_of_copying_them(self):
        catalog = load_provider_catalog(); models = []
        for model in catalog.models:
            if model.id == 'siliconflow-qwen-image':
                operation = model.operations['text_to_image']; params = dict(operation.params)
                params['steps'] = replace(params['steps'], default=25)
                params['guidance'] = replace(params['guidance'], default=3.0)
                model = replace(model, operations={'text_to_image': replace(operation, params=params)})
            models.append(model)
        with patch('angemedia_gateway.providers.image.siliconflow.load_provider_catalog', return_value=replace(catalog, models=tuple(models))):
            payload = build_siliconflow_image_payload(self.request(), self.target())
        self.assertEqual((payload['num_inference_steps'], payload['cfg']), (25, 3.0))

    def test_actual_json_maps_cfg_preserves_seed_zero_and_does_not_override_watermark(self):
        provider, _, calls = self.clients()
        req = self.request(size='1664x928', guidance=0.1, steps=1, seed=0, negative_prompt='模糊')
        with patch('angemedia_gateway.providers.image.siliconflow.provider_client', new=provider):
            result = asyncio.run(SiliconFlowProvider().generate(req, self.target()))
        self.assertEqual(calls[0]['json'], {'model': 'Qwen/Qwen-Image', 'prompt': req.prompt, 'image_size': '1664x928', 'negative_prompt': '模糊', 'seed': 0, 'num_inference_steps': 1, 'cfg': 0.1})
        self.assertEqual(calls[0]['url'], 'https://siliconflow.example.test/v1/images/generations')
        self.assertEqual(result['data'][0]['url'], REMOTE_URL)
        self.assertIsNone(calls[0]['watermark_header'])

    def test_existing_kolors_and_edit_payloads_remain_separate(self):
        kolors = build_siliconflow_image_payload(ImageRequest(prompt='旧型号', model='kolors'), RouteTarget('siliconflow', 'Kwai-Kolors/Kolors'))
        self.assertEqual((kolors['guidance_scale'], kolors['num_inference_steps']), (7.5, 20))
        self.assertNotIn('batch_size', kolors)
        self.assertNotIn('cfg', kolors)
        inline = 'data:image/png;base64,' + base64.b64encode(PNG).decode()
        edit = build_siliconflow_image_payload(ImageRequest(prompt='旧编辑', operation='edit', image=inline), RouteTarget('siliconflow', 'Qwen/Qwen-Image-Edit-2509'))
        self.assertNotIn('cfg', edit); self.assertNotIn('image_size', edit); self.assertEqual(edit['num_inference_steps'], 20)

    def test_unsupported_operations_and_output_controls_fail_before_jobs_or_http(self):
        (self.uploads/'ref.png').write_bytes(PNG)
        values = ({'operation': 'edit'}, {'image': '/uploads/ref.png'}, {'reference_images': ['/uploads/ref.png']}, {'mask': '/uploads/ref.png'}, {'quality': 'high'}, {'output_format': 'png'}, {'watermark': False})
        provider, _, calls = self.clients()
        for options in values:
            with self.subTest(options=options), self.assertRaises(InvalidImageRequest):
                ImageJobAdmissionService().prepare(self.request(**options))
            with self.subTest(adapter=options), patch('angemedia_gateway.providers.image.siliconflow.provider_client', new=provider), self.assertRaises(ValueError):
                asyncio.run(SiliconFlowProvider().generate(self.request(**options), self.target()))
        self.assertEqual(self.counts(), (0, 0, 0)); self.assertEqual(calls, [])

    def test_model_bounds_and_malformed_sizes_reject_without_admission(self):
        for options in ({'guidance': 0}, {'guidance': 20.1}, {'steps': 101}, {'seed': -1}, {'seed': 10000000000}, {'size': 'bad'}, {'size': '0x1000'}):
            with self.subTest(options=options), self.assertRaises(InvalidImageRequest):
                ImageJobAdmissionService().prepare(self.request(**options))
        ImageJobAdmissionService().prepare(self.request(steps=100, guidance=20, seed=9999999999))
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_malformed_response_shapes_are_safe_protocol_errors(self):
        for body in ({}, {'images': []}, {'images': 'bad'}, {'images': [None]}, {'images': [[]]}, {'images': [{'url': 42}]}, {'images': [{'url': ''}]}, {'images': [{'url': 'file:///private.png'}]}, {'images': [{'url': 'https://[invalid'}]}):
            provider, _, calls = self.clients(body)
            with self.subTest(body=body), patch('angemedia_gateway.providers.image.siliconflow.provider_client', new=provider), self.assertRaises(ProviderProtocolError):
                asyncio.run(SiliconFlowProvider().generate(self.request(), self.target()))
            self.assertEqual(len(calls), 1)

    def test_provider_error_statuses_do_not_leak_bodies_or_retry_generation(self):
        for status in (401, 403, 429, 500):
            provider, _, calls = self.clients({'message': 'fixture-private-body'}, status=status)
            with self.subTest(status=status), patch('angemedia_gateway.providers.image.siliconflow.provider_client', new=provider), self.assertRaises(ProviderError) as caught:
                asyncio.run(SiliconFlowProvider().generate(self.request(), self.target()))
            self.assertNotIn('fixture-private-body', str(caught.exception)); self.assertEqual(len(calls), 1)
            self.assertEqual(caught.exception.status_code, status)

    def test_confirmed_worker_downloads_once_registers_one_asset_and_replays_without_http(self):
        req = self.request(size='1664x928', steps=50, guidance=4.0, seed=0)
        action = self.propose(request=req.model_dump(exclude_none=True))
        self.assertTrue(action['review']['allowed']); self.assertIn('may_incur_cost', action['review']['required_acknowledgements'])
        self.assertEqual(self.counts(), (0, 0, 0))
        job = self.job(action); provider, download, calls = self.clients(); runtime = self.runtime(); message = self.message(job['id'])
        with patch('angemedia_gateway.providers.image.siliconflow.provider_client', new=provider), patch('angemedia_gateway.media._remote_media_http_client', new=download):
            self.assertEqual(runtime.handle(message)['status'], 'succeeded')
            self.assertEqual(runtime.handle(message)['status'], 'duplicate')
        self.assertEqual([row['method'] for row in calls], ['POST', 'GET'])
        self.assertIsNone(calls[1]['authorization'])
        assets = list_assets(job_id=job['id']); self.assertEqual(len(assets), 1)
        self.assertEqual((self.output/assets[0]['relative_path']).read_bytes(), PNG)
        self.assertEqual(assets[0]['model'], 'Qwen/Qwen-Image')
        current = get_job(job['id']); self.assertEqual(current['model'], 'Qwen/Qwen-Image')
        self.assertIn('/generated/', current['output_json'])
        self.observation = {'fixture_only': True, 'actual_provider_or_llm_calls': 0, 'production_database_read': False,
            'jobs_before_confirmation': 0, 'upstream_model': 'Qwen/Qwen-Image', 'image_size': '1664x928', 'cfg': 4.0,
            'provider_posts': 1, 'safe_downloads': 1, 'assets': 1, 'replay': 'duplicate', 'watermark_default_preserved': True,
            'original_localizer_worker_assets': True, 'download_provider_authorization_forwarded': False}

    def test_private_output_redirect_cannot_be_imported_as_a_successful_asset(self):
        job = self.job(self.propose(request=self.request().model_dump(exclude_none=True)))
        provider, download, calls = self.clients(redirect='http://127.0.0.1/private.png')
        with patch('angemedia_gateway.providers.image.siliconflow.provider_client', new=provider), patch('angemedia_gateway.media._remote_media_http_client', new=download):
            self.assertEqual(self.runtime().handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual([row['method'] for row in calls], ['POST', 'GET']); self.assertEqual(list_assets(job_id=job['id']), [])
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM generations WHERE job_id=?', (job['id'],)).fetchone()[0], 0)

    def test_changed_cfg_after_confirmation_is_blocked_before_provider_submission(self):
        job = self.job(self.propose(request=self.request(guidance=4).model_dump(exclude_none=True)))
        with closing(db_connect()) as conn:
            payload = json.loads(job['input_json']); payload['request']['guidance'] = 8
            conn.execute('UPDATE jobs SET input_json=? WHERE id=?', (json.dumps(payload), job['id'])); conn.commit()
        provider, _, calls = self.clients()
        with patch('angemedia_gateway.providers.image.siliconflow.provider_client', new=provider):
            self.assert_rejected(self.runtime(), get_job(job['id']))
        self.assertEqual(calls, [])

    def test_changed_account_before_execution_cannot_submit_under_a_different_key(self):
        job = self.job(self.propose(request=self.request().model_dump(exclude_none=True)))
        update_provider_runtime_config('siliconflow', api_key='fixture-rotated-siliconflow-key')
        provider, _, calls = self.clients()
        with patch('angemedia_gateway.providers.image.siliconflow.provider_client', new=provider):
            self.assert_rejected(self.runtime(), job)
        self.assertEqual(calls, [])

    def test_original_account_remains_frozen_when_configuration_changes_after_execution_starts(self):
        job = self.job(self.propose(request=self.request().model_dump(exclude_none=True)))
        provider, download, calls = self.clients()
        class ChangeAfterStart:
            async def generate(inner, request, target):
                update_provider_runtime_config('siliconflow', api_key='fixture-rotated-key', base_url_override='https://changed.invalid/v1')
                return await SiliconFlowProvider().generate(request, target)
        with patch('angemedia_gateway.providers.image.siliconflow.provider_client', new=provider), patch('angemedia_gateway.media._remote_media_http_client', new=download):
            self.assertEqual(self.runtime({'siliconflow': ChangeAfterStart()}).handle(self.message(job['id']))['status'], 'succeeded')
        self.assertEqual(calls[0]['url'], 'https://siliconflow.example.test/v1/images/generations')
        self.assertEqual(calls[0]['authorization'], 'Bearer fixture-siliconflow-credential-123456789')

    def test_same_semantics_deduplicate_but_cfg_and_size_remain_distinct(self):
        admission = ImageJobAdmissionService(); first = admission.submit(self.request(guidance=4, size='1664x928'))
        same = admission.submit(self.request(guidance=4, size='1664x928'))
        cfg = admission.submit(self.request(guidance=8, size='1664x928'))
        size = admission.submit(self.request(guidance=4, size='1328x1328'))
        self.assertEqual(first.job['id'], same.job['id']); self.assertEqual(len({first.job['id'], cfg.job['id'], size.job['id']}), 3)

    def test_public_catalog_drives_existing_ui_without_hardcoded_qwen_controls(self):
        models = catalog_api_response(load_provider_catalog())['models']; model = next(row for row in models if row['id'] == 'siliconflow-qwen-image')
        script = """
        import assert from 'node:assert/strict';import fs from 'node:fs';
        import {supportedParamNames,sizeOptionsForModel,supportsImageEdit} from './operation-capabilities.js';
        import {buildOperationPayload} from './operation-payload.js';
        const model=JSON.parse(fs.readFileSync(0,'utf8'));
        assert.deepEqual(supportedParamNames(model).sort(),['guidance','negative_prompt','prompt','seed','size','steps']);
        assert.equal(supportsImageEdit(model),false);assert.equal(sizeOptionsForModel(model).at(-1).value,'custom');
        assert.deepEqual(buildOperationPayload(model,{operation:'generate',guidance:'0.1',steps:'1',seed:'0',image:'/uploads/ignored.png',output_format:'webp'}),
          {operation:'generate',guidance:0.1,steps:1,seed:0});
        console.log(JSON.stringify({ok:true}));
        """
        self.assertTrue(run_operation_helper_script(script, model)['ok'])
