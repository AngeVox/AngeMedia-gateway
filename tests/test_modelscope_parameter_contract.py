"""型号参数、候选规划与真实确认链的受控合同；不调用外部生成服务。"""
import asyncio
import base64
from contextlib import asynccontextmanager, closing, ExitStack
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.providers.catalog.api import catalog_api_response
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.providers.catalog.validation import CatalogOperationValidationError
from angemedia_gateway.providers.errors import BackendUnavailable, ProviderValidationError
from angemedia_gateway.providers.image.modelscope import ModelScopeProvider, build_modelscope_image_payload
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.schemas import ImageRequest
from angemedia_gateway.services.image_execution import build_image_execution_plan, InvalidImageRequest
from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService
from test_modelscope_durable_pipeline import _runtime
from test_siliconflow_qwen_image_contract import PNG, REMOTE_URL
from test_web_studio_generate_image_operation_controls import run_operation_helper_script

MODULE = 'angemedia_gateway.providers.image.modelscope'
MODELS = {
    'qwen': 'Qwen/Qwen-Image-2512',
    'qwen-edit-2511': 'Qwen/Qwen-Image-Edit-2511',
    'flux': 'black-forest-labs/FLUX.1-Krea-dev',
    'z-image': 'Tongyi-MAI/Z-Image',
    'z-turbo': 'Tongyi-MAI/Z-Image-Turbo',
}


class ModelScopeParameterContractTest(ActionWorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        update_provider_runtime_config('modelscope', enabled=True,
            api_key='fixture-modelscope-parameter-123456789', base_url_override='https://modelscope.example.test')
        self.runtime, _ = _runtime()
        item = patch(MODULE + '.quota.try_acquire', new=AsyncMock(return_value=True))
        self.budget = item.start(); self.addCleanup(item.stop)

    def payload(self, alias='qwen', **values):
        return build_modelscope_image_payload(ImageRequest(prompt='秋日山谷', **values),
            RouteTarget('modelscope', MODELS.get(alias, alias)))

    def references(self, count=3):
        result = []
        for index in range(count):
            name = f'ref-{index}.png'
            (self.uploads / name).write_bytes(PNG + bytes([index]))
            result.append('/uploads/' + name)
        return result

    def clients(self):
        calls = []
        def reply(request):
            calls.append({'method': request.method, 'url': str(request.url),
                'authorization': request.headers.get('authorization'),
                'json': json.loads(request.content) if request.content else None})
            if request.method == 'POST': return httpx.Response(200, json={'task_id': 'parameter-task'})
            if '/v1/tasks/' in request.url.path:
                return httpx.Response(200, json={'task_status': 'SUCCEED', 'output_images': [REMOTE_URL]})
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        @asynccontextmanager
        async def provider(**kwargs):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client: yield client
        stack = ExitStack()
        stack.enter_context(patch(MODULE + '.provider_client', new=provider))
        stack.enter_context(patch('angemedia_gateway.media._remote_media_http_client',
            new=lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply))))
        return stack, calls

    def plan(self, request, aliases):
        return build_image_execution_plan(request, resolve_chain_func=lambda _: [
            RouteTarget('modelscope', MODELS[alias]) for alias in aliases])

    def test_catalog_declares_explicit_common_controls_without_injecting_defaults(self):
        entries = {m.id: m for m in load_provider_catalog().models}
        for alias, native in MODELS.items():
            with self.subTest(alias=alias):
                model = entries[alias]; self.assertEqual(model.provider_model, native)
                operation = model.operations['image_edit' if 'edit' in alias else 'text_to_image']
                for name in ('seed', 'steps'):
                    self.assertEqual(operation.params[name].provider_field, name)
                    self.assertIsNone(operation.params[name].default)
                self.assertNotIn('guidance', operation.params)
                if 'edit' not in alias:
                    self.assertEqual(self.payload(alias), {'model': native, 'n': 1, 'prompt': '秋日山谷', 'size': '1024x1024'})

    def test_actual_known_submit_uses_channel_field_names_and_keeps_seed_zero(self):
        stack, calls = self.clients()
        with stack:
            task = asyncio.run(ModelScopeProvider().submit(ImageRequest(prompt='山谷', seed=0, steps=9,
                negative_prompt='模糊'), RouteTarget('modelscope', MODELS['qwen'])))
        self.assertEqual(task, 'parameter-task'); self.budget.assert_awaited_once()
        self.assertEqual(calls[0]['json'], {'model': MODELS['qwen'], 'n': 1, 'prompt': '山谷',
            'size': '1024x1024', 'seed': 0, 'steps': 9, 'negative_prompt': '模糊'})

    def test_all_five_models_forward_seed_and_steps_without_native_pipeline_names(self):
        for alias in MODELS:
            values = {'operation': 'edit', 'reference_images': self.references(1)} if 'edit' in alias else {}
            with self.subTest(alias=alias):
                result = self.payload(alias, seed=2147483647, steps=100, **values)
                self.assertEqual((result['seed'], result['steps']), (2147483647, 100))
                self.assertNotIn('num_inference_steps', result); self.assertNotIn('generator', result)

    def test_negative_prompt_is_declared_only_where_model_evidence_supports_it(self):
        for alias in ('qwen', 'z-image', 'qwen-edit-2511'):
            values = {'reference_images': self.references(1)} if 'edit' in alias else {}
            self.assertEqual(self.payload(alias, negative_prompt='低质量', **values)['negative_prompt'], '低质量')
        for alias in ('flux', 'z-turbo'):
            with self.subTest(alias=alias), self.assertRaises(CatalogOperationValidationError):
                self.payload(alias, negative_prompt='低质量')

    def test_documented_freeform_dimension_edges_are_sent_without_rounding(self):
        for alias, low, high in (('qwen', 64, 1664), ('flux', 64, 1024), ('z-turbo', 512, 2048)):
            for size in (f'{low}x{high}', f'{low+1}x{high-1}'):
                with self.subTest(alias=alias, size=size): self.assertEqual(self.payload(alias, size=size)['size'], size)
            for size in (f'{low-1}x{high}', f'{low}x{high+1}'):
                with self.subTest(alias=alias, size=size), self.assertRaises(CatalogOperationValidationError): self.payload(alias, size=size)

    def test_base_z_image_retains_previous_area_bounds(self):
        self.assertEqual(self.payload('z-image', size='1024x768')['size'], '1024x768')
        with self.assertRaises(CatalogOperationValidationError): self.payload('z-image', size='64x64')

    def test_edit_keeps_reference_order_and_omits_implicit_size(self):
        refs = self.references()
        result = self.payload('qwen-edit-2511', image=refs[0], reference_images=refs[1:], seed=0, steps=40,
            negative_prompt='模糊')
        actual = [base64.b64decode(value.split(',', 1)[1]) for value in result['image_url']]
        self.assertEqual(actual, [PNG + bytes([index]) for index in range(3)])
        self.assertNotIn('size', result); self.assertNotIn('image', result)

    def test_native_and_legacy_direct_reference_aliases_are_validated_and_counted(self):
        refs = self.references()
        result = self.payload('qwen-edit-2511', image=refs[0], images=[refs[1]], image_url=refs[2])
        self.assertEqual([base64.b64decode(v.split(',', 1)[1]) for v in result['image_url']],
            [PNG + bytes([index]) for index in range(3)])
        for values in ({'reference_images': [refs[0]] * 10, 'image_url': refs[1]}, {'image_url': 7}):
            with self.subTest(values=values), self.assertRaises(CatalogOperationValidationError):
                self.payload('qwen-edit-2511', **values)

    def test_direct_aliases_do_not_expand_durable_admission_contract(self):
        ref = self.references(1)[0]
        for field in ('images', 'image_url'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'unsupported queued image fields'):
                ImageJobAdmissionService().prepare(ImageRequest(prompt='山谷', model='qwen-edit-2511', **{field: [ref]}))

    def test_edit_size_and_mask_are_not_invented(self):
        ref = self.references(1)[0]
        for values in ({'size': '1024x1024'}, {'mask': ref}):
            with self.subTest(values=values), self.assertRaises(CatalogOperationValidationError):
                self.payload('qwen-edit-2511', reference_images=[ref], **values)

    def test_mixed_remote_and_data_references_remain_explicitly_unsupported(self):
        with self.assertRaisesRegex(BackendUnavailable, 'mixed local and remote'):
            self.payload('qwen-edit-2511', reference_images=[self.references(1)[0], 'https://example.com/ref.png'])

    def test_common_parameter_bounds_fail_before_budget_or_http(self):
        stack, calls = self.clients()
        with stack:
            for values in ({'seed': -1}, {'seed': 2147483648}, {'steps': 101}, {'guidance': 4}):
                with self.subTest(values=values), self.assertRaises(ProviderValidationError) as caught:
                    asyncio.run(ModelScopeProvider().submit(ImageRequest(prompt='山谷', **values), RouteTarget('modelscope', MODELS['qwen'])))
                self.assertFalse(caught.exception.request_committed)
        with self.assertRaises(ValueError): ImageRequest(prompt='山谷', steps=0)
        self.assertEqual(calls, []); self.budget.assert_not_awaited()

    def test_common_prompt_character_limits_have_exact_edges(self):
        target = RouteTarget('modelscope', MODELS['qwen'])
        result = build_modelscope_image_payload(ImageRequest(prompt='山' * 1999, negative_prompt='雨' * 3999), target)
        self.assertEqual(len(result['prompt']), 1999); self.assertEqual(len(result['negative_prompt']), 3999)
        for values in ({'prompt': '山' * 2000}, {'prompt': '山', 'negative_prompt': '雨' * 4000}):
            with self.subTest(field=max(values, key=lambda k: len(values[k]))), self.assertRaises(CatalogOperationValidationError):
                build_modelscope_image_payload(ImageRequest(**values), target)

    def test_undeclared_controls_are_rejected_instead_of_silently_ignored(self):
        for values in ({'guidance_scale': 0}, {'true_cfg_scale': 4}, {'loras': ['x']}, {'extra_body': {'steps': 8}},
                {'width': 1024}, {'safe': False}, {'user': 'alice'}):
            with self.subTest(values=values), self.assertRaises(CatalogOperationValidationError): self.payload(**values)

    def test_unknown_native_model_preserves_explicit_common_envelope_without_guessed_caps(self):
        self.assertEqual(self.payload('org/unverified', size='2500x3000', seed=0, steps=9, negative_prompt='模糊'),
            {'model': 'org/unverified', 'n': 1, 'prompt': '秋日山谷', 'size': '2500x3000', 'seed': 0, 'steps': 9, 'negative_prompt': '模糊'})
        self.assertEqual(set(self.payload('org/unverified')), {'model', 'n', 'prompt', 'size'})

    def test_unknown_native_edit_and_undeclared_controls_are_not_assumed(self):
        for values in ({'operation': 'edit'}, {'image_url': 'https://example.com/ref.png'}, {'guidance': 4},
                {'mask': '/uploads/ref.png'}, {'reference_images': ['/uploads/ref.png']}, {'size': 'auto'}):
            with self.subTest(values=values), self.assertRaises(CatalogOperationValidationError): self.payload('org/unverified', **values)

    def test_default_candidates_are_filtered_by_parameters_in_original_order(self):
        aliases = ['flux', 'qwen', 'z-turbo', 'z-image']
        plan = self.plan(ImageRequest(prompt='山谷', negative_prompt='模糊'), aliases)
        self.assertEqual(plan.routes, tuple(('modelscope', MODELS[a]) for a in ('qwen', 'z-image')))
        self.assertEqual(self.plan(ImageRequest(prompt='山谷', seed=0, steps=9), aliases).routes,
            tuple(('modelscope', MODELS[a]) for a in aliases))

    def test_explicit_model_never_silently_chooses_another_candidate(self):
        with self.assertRaises(InvalidImageRequest):
            self.plan(ImageRequest(prompt='山谷', model='flux', negative_prompt='模糊'), ['flux'])

    def test_no_compatible_default_candidate_fails_without_creating_job(self):
        before = self.counts()
        with self.assertRaisesRegex(InvalidImageRequest, 'no default image model supports'):
            self.plan(ImageRequest(prompt='山谷', negative_prompt='模糊'), ['flux', 'z-turbo'])
        self.assertEqual(self.counts(), before); self.budget.assert_not_awaited()

    def test_modelscope_length_limit_does_not_restrict_compatible_other_provider(self):
        chain = [RouteTarget('modelscope', MODELS['qwen']), RouteTarget('siliconflow', 'Kwai-Kolors/Kolors')]
        plan = build_image_execution_plan(ImageRequest(prompt='山' * 2000), resolve_chain_func=lambda _: chain)
        self.assertEqual(plan.routes, (('siliconflow', 'Kwai-Kolors/Kolors'),))

    def test_actual_filtered_routes_and_parameters_are_bound_to_existing_request_hash(self):
        chain = [RouteTarget('modelscope', MODELS[a]) for a in ('flux', 'qwen', 'z-turbo')]
        service = ImageJobAdmissionService(resolve_chain_func=lambda _: chain)
        first = service.prepare(ImageRequest(prompt='山谷', negative_prompt='模糊', seed=0)).arguments()
        second = service.prepare(ImageRequest(prompt='山谷', negative_prompt='模糊', seed=1)).arguments()
        third = ImageJobAdmissionService(resolve_chain_func=lambda _: chain[1:]).prepare(ImageRequest(prompt='山谷', negative_prompt='模糊', seed=0)).arguments()
        self.assertEqual(first['payload']['route']['routes'], [{'provider': 'modelscope', 'model': MODELS['qwen']}])
        self.assertNotEqual(first['request_hash'], second['request_hash'])
        self.assertEqual(first['request_hash'], third['request_hash']); self.assertEqual(first['request_hash_version'], third['request_hash_version'])

    def test_existing_frontend_helpers_preserve_zero_and_omit_blank_or_unsupported_controls(self):
        models = {m['id']: m for m in catalog_api_response(load_provider_catalog())['models']}
        script = """
        import fs from 'node:fs';
        import {buildOperationPayload} from './operation-payload.js';
        const {models}=JSON.parse(fs.readFileSync(0,'utf8'));
        const result={};
        for(const [id,model] of Object.entries(models)) result[id]={
          filled:buildOperationPayload(model,{operation:'generate',seed:'0',steps:'9',negative_prompt:'模糊'}),
          blank:buildOperationPayload(model,{operation:'generate',seed:'',steps:'',negative_prompt:''})};
        console.log(JSON.stringify(result));
        """
        result = run_operation_helper_script(script, {'models': {a: models[a] for a in ('qwen', 'flux', 'z-turbo')}})
        self.assertEqual(result['qwen']['filled'], {'operation': 'generate', 'seed': 0, 'steps': 9, 'negative_prompt': '模糊'})
        for alias in ('flux', 'z-turbo'): self.assertNotIn('negative_prompt', result[alias]['filled'])
        for item in result.values(): self.assertEqual(item['blank'], {'operation': 'generate'})

    def test_confirmed_known_parameters_reach_post_poll_asset_and_replay_once(self):
        action = self.propose(request={'prompt': '秋日山谷', 'model': 'qwen', 'seed': 0, 'steps': 9, 'negative_prompt': '模糊'})
        self.assertEqual(self.counts(), (0, 0, 0))
        job = self.job(action); original = self.message(job['id']); stack, calls = self.clients()
        with stack:
            for _ in range(3): self.runtime.handle(self.message(job['id']))
            self.assertEqual(self.runtime.handle(original)['status'], 'duplicate')
        self.assertEqual(get_job(job['id'])['status'], 'succeeded'); self.assertEqual(len(list_assets(job_id=job['id'])), 1)
        self.assertEqual([r['method'] for r in calls], ['POST', 'GET', 'GET']); self.assertIsNone(calls[-1]['authorization'])
        self.assertEqual({k: calls[0]['json'][k] for k in ('seed', 'steps', 'negative_prompt')}, {'seed': 0, 'steps': 9, 'negative_prompt': '模糊'})
        self.observation = {'fixture_only': True, 'actual_generation_or_llm_calls': 0, 'production_database_read': False,
            'provider_posts': 1, 'provider_polls': 1, 'safe_downloads': 1, 'assets': 1, 'terminal_replay_http_calls': 0,
            'submitted_optional_parameters': {'seed': 0, 'steps': 9, 'negative_prompt': '模糊'},
            'no_injected_native_pipeline_defaults': True, 'original_confirmation_grant_outbox_and_assets': True}

    def test_unknown_native_confirmation_retains_unverified_acknowledgement(self):
        action = self.propose(request={'prompt': '山谷', 'model': 'modelscope::org/unverified', 'seed': 0, 'steps': 9})
        self.assertIn('unverified_model', action['review']['required_acknowledgements'])
        job = self.job(action); stack, calls = self.clients()
        with stack:
            for _ in range(3): self.runtime.handle(self.message(job['id']))
        self.assertEqual(get_job(job['id'])['status'], 'succeeded'); self.assertEqual(calls[0]['json']['model'], 'org/unverified')
        self.assertEqual(len(list_assets(job_id=job['id'])), 1)

    def test_confirmed_parameter_tampering_is_rejected_before_http(self):
        job = self.job(self.propose(request={'prompt': '山谷', 'model': 'qwen', 'seed': 0, 'steps': 9}))
        with closing(db_connect()) as conn:
            payload = json.loads(conn.execute('SELECT input_json FROM jobs WHERE id=?', (job['id'],)).fetchone()[0])
            payload['request']['steps'] = 10
            conn.execute('UPDATE jobs SET input_json=? WHERE id=?', (json.dumps(payload), job['id'])); conn.commit()
        stack, calls = self.clients()
        with stack: self.assert_rejected(self.runtime, job)
        self.assertEqual(calls, []); self.budget.assert_not_awaited()
