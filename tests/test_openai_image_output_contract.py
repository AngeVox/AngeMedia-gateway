"""输出选项贯穿目录、哈希、真实HTTP序列化、确认动作与原资产链路。"""
import asyncio
import base64
from contextlib import asynccontextmanager, closing
from email import policy
from email.parser import BytesParser
from io import BytesIO
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from PIL import Image, ImageDraw
from pydantic import ValidationError

from assistant_action_worker_support import ActionWorkerFixture
from studio_test_client import TestClient
from angemedia_gateway import config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.image_markup import ImageMarkupError, validate_image_markup
from angemedia_gateway.media_response import decode_image_b64
from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.providers.catalog.api import catalog_api_response
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.providers.catalog.validation import CatalogOperationValidationError
from angemedia_gateway.providers.custom_capabilities import validate_custom_image_request
from angemedia_gateway.providers.errors import BackendUnavailable, ProviderProtocolError
from angemedia_gateway.providers.image.openai_compatible import OpenAICompatibleImageProvider
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.generations import get_generation_by_job_id
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.request_hash import compute_request_hash
from angemedia_gateway.request_hash_builders import build_image_request_hash_payload, build_legacy_image_request_hash_payload_v1
from angemedia_gateway.schemas import ImageRequest
from angemedia_gateway.routes.storage import router as storage_router
from angemedia_gateway.svg_output import UnsafeSvgOutput, validate_static_svg
from angemedia_gateway.services.assistant_action_tool_executor import proposal_summary
from angemedia_gateway.services.image_execution import ImageExecutionService, InvalidImageRequest
from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService
from angemedia_gateway.services.image_job_worker import ImageJobWorker
from angemedia_gateway.services.job_stage_registry import JobStageRegistry
from angemedia_gateway.services.worker_runtime import WorkerRuntime
from test_web_studio_generate_image_operation_controls import run_studio_module_script

# 两像素静态夹具验证各种输出文件的原始字节。
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGM0anJjYGBgYgADAAvOAP55tiIrAAAAAElFTkSuQmCC')
JPEG = base64.b64decode('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDCooorgPAP/9k=')
WEBP = base64.b64decode('UklGRjQAAABXRUJQVlA4ICgAAACQAQCdASoCAAIAAUAmJaACdLoAA5gA/u52f6+AKeqP/kGPJT0NZrgA')


SVG = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
       b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80" viewBox="0 0 120 80">'
       b'<defs><linearGradient id="tone"><stop offset="0" stop-color="#024"/></linearGradient></defs>'
       b'<path d="M10 10h90v55H10z" fill="url(#tone)"/></svg>')


def gif_fixture(count=2, delay=120, size=(128, 96)):
    frames = [Image.new("P", size, color=index) for index in range(count)]
    for frame in frames:
        frame.putpalette([color for value in range(256) for color in (value, 0, 255 - value)])
    with BytesIO() as encoded:
        frames[0].save(encoded, format="GIF", save_all=True, append_images=frames[1:], duration=delay, loop=0)
        return encoded.getvalue()


def markup_fixture(size=(8, 12), orientation=6, *, overlay_format="PNG"):
    with Image.new("RGB", size, "white") as source, Image.new("RGBA", size[::-1], (0, 0, 0, 0)) as overlay:
        exif = source.getexif()
        exif[274] = orientation
        ImageDraw.Draw(overlay).rectangle((1, 1, 4, 4), outline="red")
        with BytesIO() as image_file, BytesIO() as markup_file:
            source.save(image_file, "JPEG", exif=exif)
            overlay.save(markup_file, overlay_format)
            return image_file.getvalue(), markup_file.getvalue()


class OpenAIImageOutputContractTest(ActionWorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        credential = patch.object(C, 'OPENAI_IMAGE_API_KEY', 'fixture-openai-output-credential-123456789')
        credential.start(); self.addCleanup(credential.stop)
        update_provider_runtime_config('openai_image', enabled=True, base_url_override='https://openai.example.test/v1')

    def request(self, **values):
        return ImageRequest(prompt='山谷', model='openai-flare', **values)

    def target(self):
        return RouteTarget('openai_image', 'gpt-image-2.5-flare')

    def transport(self, image=PNG, **metadata):
        calls = []
        def reply(request):
            call = {'url': str(request.url)}
            if request.headers['content-type'].startswith('multipart/form-data'):
                message = BytesParser(policy=policy.default).parsebytes(b'Content-Type: ' + request.headers['content-type'].encode() + b'\r\n\r\n' + request.content)
                call['fields'] = {p.get_param('name', header='content-disposition'): p.get_payload(decode=True).decode() for p in message.iter_parts() if not p.get_filename()}
                call['files'] = [p.get_filename() for p in message.iter_parts() if p.get_filename()]
                call['file_parts'] = [(p.get_param('name', header='content-disposition'), p.get_content_type(), p.get_payload(decode=True))
                                      for p in message.iter_parts() if p.get_filename()]
            else:
                call['json'] = json.loads(request.content)
            calls.append(call)
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(image).decode()}], **metadata})
        @asynccontextmanager
        async def client(**kwargs):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as value:
                yield value
        return client, calls

    def runtime(self):
        worker = ImageJobWorker(executor=ImageExecutionService(providers={'openai_image': OpenAICompatibleImageProvider()}))
        return WorkerRuntime(registry=JobStageRegistry({'image_generate': worker.handle}), runtime_refresher=lambda: None)

    def test_compression_schema_rejects_coercion_and_out_of_range(self):
        for value in (True, False, -1, 101, 2.5, '20'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.request(output_compression=value)
        for value in (0, 100):
            self.assertEqual(self.request(output_compression=value).output_compression, value)

    def test_catalog_declares_shared_output_options_for_both_operations(self):
        catalog = load_provider_catalog()
        for model in ('openai-image', 'openai-flare', 'openai-image-2'):
            for name in ('text_to_image', 'image_edit'):
                params = catalog.models_by_id[model].operations[name].params
                with self.subTest(model=model, operation=name):
                    self.assertEqual(params['output_format'].enum_values, ('png', 'jpeg', 'webp'))
                    self.assertEqual(params['output_format'].default, 'png')
                    self.assertEqual((params['output_compression'].min, params['output_compression'].max), (0, 100))
                    self.assertEqual(params['background'].enum_values, ('auto', 'opaque', 'transparent'))

    def test_valid_output_combinations_prepare_without_creating_jobs(self):
        ref = self.uploads/'reference.png'; ref.write_bytes(PNG)
        for model in ('openai-image', 'openai-flare', 'openai-image-2'):
            for operation in ('generate', 'edit'):
                for options in ({'background': 'transparent'}, {'output_format': 'webp', 'background': 'transparent', 'output_compression': 0}, {'output_format': 'jpeg', 'background': 'opaque', 'output_compression': 100}):
                    req = ImageRequest(prompt='山谷', model=model, operation=operation, **options, **({'image': '/uploads/reference.png'} if operation == 'edit' else {}))
                    with self.subTest(model=model, operation=operation, options=options):
                        prepared = ImageJobAdmissionService().prepare(req)
                        self.assertEqual(prepared.arguments()['payload']['request']['background'], options['background'])
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_incompatible_options_fail_before_job_or_http(self):
        for values in ({'output_format': 'jpeg', 'background': 'transparent'}, {'output_format': 'png', 'output_compression': 0}, {'output_compression': 100}):
            with self.subTest(values=values), self.assertRaises(InvalidImageRequest):
                ImageJobAdmissionService().prepare(self.request(**values))
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_other_models_custom_and_undeclared_routes_do_not_inherit_output_controls(self):
        for model in ('seedream-5-pro', 'kolors', 'modelscope::unreviewed-model'):
            entry = load_provider_catalog().models_by_id.get(model)
            target = RouteTarget(entry.provider, entry.provider_model) if entry else RouteTarget('modelscope', 'unreviewed-model')
            admission = ImageJobAdmissionService(resolve_chain_func=lambda _: [target])
            for values in ({'output_format': 'webp'}, {'background': 'opaque'}, {'output_compression': 0}):
                with self.subTest(model=model, values=values), self.assertRaises(InvalidImageRequest):
                    admission.prepare(ImageRequest(prompt='山谷', model=model, **values))
        for values in ({'output_format': 'jpeg'}, {'output_format': 'webp'}, {'background': 'auto'}, {'output_compression': 0}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_custom_image_request(self.request(**values), {'capabilities': {'image_edit': True}})

    def test_json_sends_typed_output_options_and_preserves_zero(self):
        client, calls = self.transport(WEBP, output_format='webp', background='transparent')
        req = self.request(output_format='webp', output_compression=0, background='transparent')
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client', new=client):
            result = asyncio.run(OpenAICompatibleImageProvider().generate(req, self.target()))
        self.assertEqual(result['output_format'], 'webp')
        self.assertEqual({k: calls[0]['json'][k] for k in ('output_format', 'output_compression', 'background')}, {'output_format': 'webp', 'output_compression': 0, 'background': 'transparent'})
        self.assertNotIn('response_format', calls[0]['json'])

    def test_multipart_sends_same_options_as_text_and_reuses_original_file_parts(self):
        (self.uploads/'ref.png').write_bytes(PNG)
        client, calls = self.transport(JPEG)
        req = self.request(operation='edit', image='/uploads/ref.png', output_format='jpeg', output_compression=0, background='opaque')
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client', new=client):
            asyncio.run(OpenAICompatibleImageProvider().generate(req, self.target()))
        self.assertEqual(calls[0]['files'], ['image-1.png'])
        self.assertEqual({k: calls[0]['fields'][k] for k in ('output_format', 'output_compression', 'background')}, {'output_format': 'jpeg', 'output_compression': '0', 'background': 'opaque'})
        self.assertNotIn('response_format', calls[0]['fields'])

    def test_missing_options_keep_old_request_and_response_contract(self):
        client, calls = self.transport()
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client', new=client):
            result = asyncio.run(OpenAICompatibleImageProvider().generate(self.request(), self.target()))
        self.assertNotIn('output_format', result)
        self.assertTrue({'output_format', 'output_compression', 'background'}.isdisjoint(calls[0]['json']))

    def test_response_bytes_and_metadata_must_match_explicit_format(self):
        for image, metadata in ((PNG, {}), (JPEG, {'output_format': 'png'}), (JPEG, {'output_format': []}), (b'not an image', {'output_format': 'jpeg'})):
            client, calls = self.transport(image, **metadata)
            with self.subTest(metadata=metadata), patch('angemedia_gateway.providers.image.openai_compatible.provider_client', new=client), self.assertRaises(ProviderProtocolError):
                asyncio.run(OpenAICompatibleImageProvider().generate(self.request(output_format='jpeg'), self.target()))
            self.assertEqual(len(calls), 1)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_encoded_response_decoder_keeps_size_and_signature_bounds(self):
        for value in ('!', '', base64.b64encode(b'html').decode(), base64.b64encode(PNG).decode()):
            with self.subTest(value=value[:10]), self.assertRaises(RuntimeError):
                decode_image_b64(value, max_bytes=8)

    def test_url_compatibility_does_not_claim_verifiable_output_options(self):
        data = {'data': [{'url': 'https://example.test/image.png'}]}
        self.assertEqual(OpenAICompatibleImageProvider._validated_response(data, self.request()), data)
        with self.assertRaises(ProviderProtocolError):
            OpenAICompatibleImageProvider._validated_response(data, self.request(background='opaque'))

    def test_verified_bytes_take_precedence_over_unverified_parallel_url(self):
        data = {'data': [{'b64_json': base64.b64encode(JPEG).decode(), 'url': 'https://example.test/other.png'}]}
        result = OpenAICompatibleImageProvider._validated_response(data, self.request(output_format='jpeg'))
        self.assertNotIn('url', result['data'][0])
        self.assertEqual(base64.b64decode(result['data'][0]['b64_json']), JPEG)

    def test_confirmed_worker_delivers_real_formats_once_through_original_asset_pipeline(self):
        observations = []
        for fmt, image, suffix in (('png', PNG, '.png'), ('jpeg', JPEG, '.jpg'), ('webp', WEBP, '.webp')):
            options = {'output_format': fmt, 'background': 'opaque', **({'output_compression': 0} if fmt != 'png' else {})}
            action = self.propose(request=self.request(**options).model_dump(exclude_none=True))
            for key, value in options.items(): self.assertEqual(action['review']['request'][key], value)
            before = self.counts()[0]; job = self.job(action); message = self.message(job['id'])
            self.assertEqual(self.counts()[0], before + 1)
            client, calls = self.transport(image, output_format=fmt)
            with patch('angemedia_gateway.providers.image.openai_compatible.provider_client', new=client):
                runtime = self.runtime()
                self.assertEqual(runtime.handle(message)['status'], 'succeeded')
                self.assertEqual(runtime.handle(message)['status'], 'duplicate')
            self.assertEqual(len(calls), 1)
            assets = list_assets(job_id=job['id']); self.assertEqual(len(assets), 1)
            asset = assets[0]; path = self.output/asset['relative_path']
            self.assertEqual(path.suffix, suffix); self.assertEqual(path.read_bytes(), image)
            self.assertTrue(asset['url_path'].endswith(suffix))
            current = get_job(job['id']); self.assertNotIn('b64_json": "', current['output_json'])
            observations.append({'format': fmt, 'file_extension': suffix, 'provider_posts': len(calls), 'assets': len(assets), 'replay': 'duplicate'})
        self.observation = {'fixture_only': True, 'actual_provider_or_llm_calls': 0, 'production_database_read': False, 'original_localizer_worker_assets': True, 'formats': observations}

    def test_worker_rejects_mismatched_bytes_without_recording_asset_or_completed_history(self):
        job = self.job(self.propose(request=self.request(output_format='webp').model_dump(exclude_none=True)))
        client, calls = self.transport(PNG, output_format='webp')
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client', new=client):
            self.assertEqual(self.runtime().handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual(len(calls), 1); self.assertEqual(list_assets(job_id=job['id']), [])
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM generations WHERE job_id=?', (job['id'],)).fetchone()[0], 0)

    def test_changed_output_after_confirmation_cannot_cross_execution_grant(self):
        job = self.job(self.propose(request=self.request(output_format='webp', output_compression=0).model_dump(exclude_none=True)))
        with closing(db_connect()) as conn:
            payload = json.loads(job['input_json']); payload['request']['output_compression'] = 100
            conn.execute('UPDATE jobs SET input_json=? WHERE id=?', (json.dumps(payload), job['id'])); conn.commit()
        client, calls = self.transport(WEBP)
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client', new=client):
            self.assert_rejected(self.runtime(), get_job(job['id']))
        self.assertEqual(calls, [])

    def test_output_options_have_distinct_hashes_and_cannot_reuse_legacy_v1(self):
        hashes = set()
        for options in ({}, {'background': 'auto'}, {'background': 'opaque'}, {'output_format': 'webp', 'output_compression': 0}, {'output_format': 'webp', 'output_compression': 100}):
            req = self.request(**options)
            payload = build_image_request_hash_payload(req, provider_mode='builtin', resolved_chain=[self.target()]).payload
            hashes.add(compute_request_hash(payload))
            if options:
                self.assertIsNone(build_legacy_image_request_hash_payload_v1(req, provider_mode='builtin', resolved_chain=[self.target()]).payload)
        self.assertEqual(len(hashes), 5)

    def test_unset_new_options_preserve_previous_payload_shape(self):
        req = self.request()
        payload = build_image_request_hash_payload(req, provider_mode='builtin', resolved_chain=[self.target()]).payload
        self.assertEqual(set(payload), {'kind', 'route', 'provider_mode', 'requested_model', 'prompt', 'n', 'size', 'aspect_ratio', 'response_format', 'quality', 'output_format', 'watermark', 'safe', 'negative_prompt', 'seed', 'steps', 'guidance', 'operation', 'resolved_chain'})
        self.assertEqual(payload, build_image_request_hash_payload(req.model_copy(update={'background': None, 'output_compression': None}), provider_mode='builtin', resolved_chain=[self.target()]).payload)

    def test_admission_deduplicates_same_output_but_keeps_different_compression_jobs(self):
        admission = ImageJobAdmissionService()
        first = admission.submit(self.request(output_format='webp', output_compression=0))
        second = admission.submit(self.request(output_format='webp', output_compression=100))
        replay = admission.submit(self.request(output_format='webp', output_compression=0))
        self.assertNotEqual(first.job['id'], second.job['id'])
        self.assertEqual(first.job['id'], replay.job['id'])
        self.assertEqual(self.counts()[0], 2)

    def test_ui_payload_preserves_zero_and_labels_use_all_current_locales(self):
        script = """
        import assert from 'node:assert/strict';
        import fs from 'node:fs';
        globalThis.localStorage={getItem:()=>null,setItem:()=>{}};
        globalThis.document={documentElement:{lang:''}};
        globalThis.window={dispatchEvent:()=>{}};
        const {models}=JSON.parse(fs.readFileSync(0,'utf8'));
        const {buildOperationPayload}=await import('./studio/features/generate-image/operation-payload.js');
        const {setLanguage,supportedLanguages,t}=await import('./studio/i18n.js');
        const {actionField,actionValue}=await import('./studio/lib/assistant-action-copy.js');
        assert.deepEqual(supportedLanguages,['zh-CN','en-US']);
        for(const language of supportedLanguages){
          setLanguage(language);
          for(const name of ['output_format','output_compression','background']){
            const key='generateImage.param.'+name;
            assert.notEqual(t(key),key);assert.equal(actionField(name),t(key));
          }
          for(const value of ['auto','opaque','transparent']){
            const key='generateImage.value.background.'+value;
            assert.notEqual(t(key),key);assert.equal(actionValue('background',value),t(key));
          }
        }
        for(const model of models){
          assert.deepEqual(buildOperationPayload(model,{operation:'generate',output_format:'webp',output_compression:'0',background:'transparent'}),
            {operation:'generate',output_format:'webp',output_compression:0,background:'transparent'});
        }
        console.log(JSON.stringify({ok:true}));
        """
        models = catalog_api_response(load_provider_catalog())['models']
        self.assertTrue(run_studio_module_script(script, {'models': [model for model in models if model['id'] in ('openai-image', 'openai-flare', 'openai-image-2')]})['ok'])

    def test_26_complexity_is_explicit_and_separates_durable_requests(self):
        catalog = load_provider_catalog()
        admission = ImageJobAdmissionService()
        for variant in ('sunburst', 'flare'):
            model = f'gpt-image-2.6-{variant}'
            entry = catalog.models_by_id[f'openai-26-{variant}']
            self.assertIsNone(entry.default_chain_order)
            self.assertEqual(entry.status, 'experimental')
            for name in ('text_to_image', 'image_edit'):
                spec = entry.operations[name].params['complexity']
                self.assertEqual((spec.enum_values, spec.default), (('low', 'medium', 'high'), 'high'))
            hashes = set()
            for amount in (None, 'low', 'medium', 'high'):
                req = self.request().model_copy(update={'model': model, 'size': 'auto', 'complexity': amount})
                args = admission.prepare(req).arguments()
                self.assertEqual(args['payload']['route']['routes'], [{'provider': 'openai_image', 'model': model}])
                self.assertEqual(args['payload']['request'].get('complexity'), amount)
                self.assertEqual(args['payload']['request']['size'], 'auto')
                hashes.add(args['request_hash'])
                if amount is not None:
                    self.assertIsNone(build_legacy_image_request_hash_payload_v1(
                        req, provider_mode='builtin', resolved_chain=[RouteTarget('openai_image', model)]).payload)
            self.assertEqual(len(hashes), 4)
            self.assertEqual(admission.prepare(ImageRequest(prompt='山谷', model=model, size='1536x864')).arguments()['model'], model)
        update_provider_runtime_config('bytedance', enabled=True)
        for model in ('openai-flare', 'openai_image::other-model', 'seedream-5-5', None):
            with self.subTest(model=model), self.assertRaises(InvalidImageRequest):
                admission.prepare(ImageRequest(prompt='山谷', model=model, complexity='low'))
        with self.assertRaises(ValueError):
            validate_custom_image_request(self.request(complexity='high'), {})
        self.assertEqual(self.counts()[0], 0)

    def test_26_confirmed_generate_and_edit_publish_the_requested_static_bytes(self):
        (self.uploads / 'source.png').write_bytes(PNG)
        runtime = self.runtime()
        for model in ('gpt-image-2.6-flare', 'gpt-image-2.6-sunburst'):
            for operation in ('generate', 'edit'):
                for fmt, image, suffix in (('png', PNG, '.png'), ('jpeg', JPEG, '.jpg'), ('webp', WEBP, '.webp')):
                    with self.subTest(model=model, operation=operation, format=fmt):
                        values = {'prompt': '静态合同夹具', 'model': model, 'operation': operation, 'size': 'auto',
                                  'complexity': 'medium', 'quality': 'high', 'output_format': fmt}
                        if operation == 'edit':
                            values['image'] = '/uploads/source.png'
                        action = self.propose(request=values)
                        self.assertEqual(action['review']['request']['complexity'], 'medium')
                        self.assertEqual(proposal_summary(action)['parameters']['complexity'], 'medium')
                        job = self.job(action)
                        message = self.message(job['id'])
                        client, calls = self.transport(image, output_format=fmt)
                        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client', new=client):
                            self.assertEqual(runtime.handle(message)['status'], 'succeeded')
                            self.assertEqual(runtime.handle(message)['status'], 'duplicate')
                        self.assertEqual(len(calls), 1)
                        wire = calls[0]['fields' if operation == 'edit' else 'json']
                        self.assertEqual({k: wire[k] for k in ('model', 'size', 'quality', 'complexity', 'output_format')},
                                         {k: values[k] for k in ('model', 'size', 'quality', 'complexity', 'output_format')})
                        if operation == 'edit':
                            self.assertEqual(calls[0]['files'], ['image-1.png'])
                        generation = get_generation_by_job_id(job['id'])
                        filename = generation['result_url'].rsplit('/', 1)[-1]
                        # 相同文件内容按现有资产规则复用，当前任务仍持有自己的结果链接。
                        asset, = [row for row in list_assets() if row['relative_path'] == filename]
                        path = self.output / asset['relative_path']
                        self.assertEqual((path.suffix, path.read_bytes()), (suffix, image))

    def test_26_gif_confirmation_http_and_registered_bytes_for_both_operations(self):
        (self.uploads / "source.png").write_bytes(PNG)
        animated = gif_fixture(count=3, delay=120)
        runtime = self.runtime()
        for model in ("gpt-image-2.6-flare", "gpt-image-2.6-sunburst"):
            for operation in ("generate", "edit"):
                with self.subTest(model=model, operation=operation):
                    req = {"prompt": "GIF fixture", "model": model, "output_format": "gif",
                           "operation": operation, "frame_count": 3, "frame_duration_ms": 124}
                    if operation == "edit":
                        req["image"] = "/uploads/source.png"
                    action = self.propose(request=req)
                    params = proposal_summary(action)["parameters"]
                    self.assertEqual((params["output_format"], params["frame_count"], params["frame_duration_ms"]), ("gif", 3, 124))
                    self.assertEqual(action["review"]["request"]["size"], "auto")
                    self.assertIn("may_incur_cost", action["review"]["required_acknowledgements"])
                    job = self.job(action)
                    message = self.message(job["id"])
                    client, calls = self.transport(animated, output_format="gif")
                    with patch("angemedia_gateway.providers.image.openai_compatible.provider_client", new=client):
                        self.assertEqual(runtime.handle(message)["status"], "succeeded")
                        self.assertEqual(runtime.handle(message)["status"], "duplicate")
                    call, = calls
                    if operation == "generate":
                        self.assertTrue(call["url"].endswith("/images/generations"))
                        fields = call["json"]
                        self.assertEqual((fields["frame_count"], fields["frame_duration_ms"]), (3, 124))
                    else:
                        self.assertTrue(call["url"].endswith("/images/edits"))
                        fields = call["fields"]
                        self.assertEqual((fields["frame_count"], fields["frame_duration_ms"]), ("3", "124"))
                        self.assertEqual(call["files"], ["image-1.png"])
                    self.assertEqual((fields["output_format"], fields["size"]), ("gif", "auto"))
                    generation = get_generation_by_job_id(job["id"])
                    path = self.output / generation["result_url"].rsplit("/", 1)[-1]
                    self.assertEqual((path.suffix, path.read_bytes()), (".gif", animated))
                    summary = json.loads(get_job(job["id"])["output_json"])
                    self.assertEqual((summary["frame_count"], summary["animation_duration_ms"]), (3, 360))

    def test_26_svg_confirmation_persists_same_svg_bytes_and_serves_authenticated_preview(self):
        req = {"prompt": "Vector tile", "model": "gpt-image-2.6-flare", "output_format": "svg", "quality": "high"}
        action = self.propose(request=req)
        self.assertEqual(proposal_summary(action)["parameters"]["output_format"], "svg")
        self.assertIn("may_incur_cost", action["review"]["required_acknowledgements"])
        client, calls = self.transport(SVG, output_format="svg")
        self.assertEqual(self.counts(), (0, 0, 0))
        job = self.job(action)
        with patch("angemedia_gateway.providers.image.openai_compatible.provider_client", new=client):
            self.assertEqual(self.runtime().handle(self.message(job["id"]))["status"], "succeeded")
        call, = calls
        self.assertTrue(call["url"].endswith("/images/generations"))
        self.assertEqual({key: call["json"][key] for key in ("model", "output_format", "quality", "n")},
                         {"model": req["model"], "output_format": "svg", "quality": "high", "n": 1})
        generation = get_generation_by_job_id(job["id"])
        name = generation["result_url"].rsplit("/", 1)[-1]
        path = self.output / name
        self.assertEqual((path.suffix, path.read_bytes()), (".svg", SVG))
        self.assertEqual(json.loads(get_job(job["id"])["output_json"])["output_format"], "svg")
        self.assertEqual([row["filename"] for row in list_assets()], [name])

        app = FastAPI()
        app.include_router(storage_router)
        href = f"/v1/admin/media/generated/{name}"
        with TestClient(app) as browser:
            self.assertEqual(browser.get(href, params={"preview": "true"}).status_code, 401)
            browser.seed_session(self.cookies["alice"])
            preview = browser.get(href, params={"preview": "true"})
            self.assertEqual((preview.status_code, preview.content), (200, SVG))
            self.assertTrue(preview.headers["content-type"].startswith("image/svg+xml"))
            self.assertIn("sandbox", preview.headers["content-security-policy"])
            self.assertEqual(preview.headers["x-content-type-options"], "nosniff")
            self.assertEqual(preview.headers["cache-control"], "private, no-store")
            for url in (href, f"/generated/{name}"):
                download = browser.get(url)
                self.assertEqual((download.status_code, download.content), (200, SVG))
                self.assertEqual(download.headers["content-type"], "application/octet-stream")
                self.assertIn("attachment", download.headers["content-disposition"])
                head = browser.head(url)
                self.assertEqual((head.status_code, head.content, int(head.headers["content-length"])), (200, b"", len(SVG)))
            path.write_bytes(b"<unavailable/>")
            self.assertEqual(browser.get(href, params={"preview": "true"}).status_code, 404)
            self.assertEqual(browser.get(f"/generated/{name}").status_code, 404)

    def test_svg_generation_contract_does_not_admit_edit_or_author_options(self):
        (self.uploads / "source.png").write_bytes(PNG)
        args = {"prompt": "Vector tile", "model": "gpt-image-2.6-sunburst", "output_format": "svg"}
        for extra in ({"operation": "edit"}, {"image": "/uploads/source.png"}, {"output_compression": 50},
                      {"stream": True}, {"partial_images": 1}, {"svg": {"model": "fixture"}},
                      {"background": "transparent"}, {"frame_count": 2}, {"model": "openai-flare"},
                      {"model": "openai_image::unverified"}):
            with self.subTest(extra=extra), self.assertRaises((InvalidImageRequest, ValueError)):
                ImageJobAdmissionService().prepare(ImageRequest(**(args | extra)))
        prepared = ImageJobAdmissionService().prepare(ImageRequest(**args)).arguments()
        png = ImageJobAdmissionService().prepare(ImageRequest(**(args | {"output_format": "png"}))).arguments()
        self.assertNotEqual(prepared["request_hash"], png["request_hash"])
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_svg_output_refuses_documents_outside_the_static_vector_contract(self):
        validate_static_svg(SVG)
        options = {
            "raster": PNG, "truncated": SVG[:-5], "different_encoding": SVG.decode().encode("utf-16"),
            "namespace": SVG.replace(b"http://www.w3.org/2000/svg", b"urn:not-svg"),
            "missing_gradient": SVG.replace(b"url(#tone)", b"url(#missing)"),
            "embedded_image": SVG.replace(b"</svg>", b"<image/></svg>"),
            "animation": SVG.replace(b"</svg>", b"<animate/></svg>"),
            "style_sheet": SVG.replace(b"</svg>", b"<style/></svg>"),
            "oversize_canvas": SVG.replace(b'width="120"', b'width="10000"'),
        }
        for name, content in options.items():
            with self.subTest(name=name), self.assertRaises(UnsafeSvgOutput):
                validate_static_svg(content)
        req = ImageRequest(prompt="Vector", model="gpt-image-2.6-sunburst", output_format="svg")
        client, calls = self.transport(PNG, output_format="svg")
        with patch("angemedia_gateway.providers.image.openai_compatible.provider_client", new=client):
            with self.assertRaises(ProviderProtocolError) as caught:
                asyncio.run(OpenAICompatibleImageProvider().generate(req, RouteTarget("openai_image", req.model)))
        self.assertTrue(caught.exception.request_committed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(list(self.output.iterdir()), [])
        with self.assertRaises(RuntimeError):
            decode_image_b64(base64.b64encode(SVG).decode())

    def test_26_gif_omits_upstream_defaults_and_rejects_incorrect_outputs(self):
        model = "gpt-image-2.6-flare"
        req = ImageRequest(prompt="GIF fixture", model=model, output_format="gif")
        target = RouteTarget("openai_image", model)
        client, calls = self.transport(gif_fixture(count=9, delay=200))
        with patch("angemedia_gateway.providers.image.openai_compatible.provider_client", new=client):
            data = asyncio.run(OpenAICompatibleImageProvider().generate(req, target))
        self.assertEqual((data["frame_count"], data["animation_duration_ms"]), (9, 1800))
        self.assertEqual(calls[0]["json"]["size"], "auto")
        self.assertFalse({"frame_count", "frame_duration_ms"} & calls[0]["json"].keys())
        request = ImageRequest(prompt="GIF fixture", model=model, output_format="gif", frame_count=2, frame_duration_ms=120)
        good = gif_fixture()
        mutations = (PNG, gif_fixture(count=1), gif_fixture(count=3), gif_fixture(delay=130), good[:-1],
                     good[:6] + b"\xff\xff\xff\xff" + good[10:])
        for index, content in enumerate(mutations):
            with self.subTest(case=index), self.assertRaises(ProviderProtocolError):
                OpenAICompatibleImageProvider._validated_response(
                    {"data": [{"b64_json": base64.b64encode(content).decode()}]}, request, native_contract=True)
        with self.assertRaises(ProviderProtocolError):
            OpenAICompatibleImageProvider._validated_response(
                {"data": [{"b64_json": base64.b64encode(good).decode()}]},
                request.model_copy(update={"size": "1024x1024"}), native_contract=True)

    def test_26_gif_parameters_are_strict_before_admission_and_form_distinct_hashes(self):
        (self.uploads / "source.png").write_bytes(PNG)
        admission = ImageJobAdmissionService()
        base = {"prompt": "GIF fixture", "model": "gpt-image-2.6-flare", "output_format": "gif"}
        hashes = {admission.prepare(ImageRequest(**(base | params))).arguments()["request_hash"]
                  for params in ({}, {"frame_count": 4}, {"frame_duration_ms": 120}, {"frame_duration_ms": 130})}
        self.assertEqual(len(hashes), 4)
        for changes in ({"frame_count": 1}, {"frame_count": True}, {"frame_count": "4"},
                        {"frame_count": 17}, {"frame_duration_ms": 19}, {"frame_duration_ms": 2001}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                ImageRequest(**(base | changes))
        incompatible = ({"model": "openai-flare"}, {"model": "openai_image::unknown"}, {"model": None},
                        {"output_format": "png", "frame_count": 4}, {"mask": "/uploads/source.png"},
                        {"output_compression": 10},
                        {"background": "transparent"}, {"size": "1025x1024"})
        for changes in incompatible:
            with self.subTest(changes=changes), self.assertRaises(InvalidImageRequest):
                admission.prepare(ImageRequest(**(base | changes)))
        for changes in ({"partial_images": 1}, {"stream": True}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "unsupported queued image fields"):
                admission.prepare(ImageRequest(**(base | changes)))
        with self.assertRaises(ValueError):
            validate_custom_image_request(ImageRequest(**base), {})
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_26_omitted_complexity_leaves_upstream_default_and_requires_native_png_data(self):
        model = 'gpt-image-2.6-flare'
        req = ImageRequest(prompt='山谷', model=model)
        target = RouteTarget('openai_image', model)
        client, calls = self.transport()
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client', new=client):
            result = asyncio.run(OpenAICompatibleImageProvider().generate(req, target))
        self.assertNotIn('complexity', calls[0]['json'])
        self.assertEqual(result['output_format'], 'png')
        malformed = (
            {'data': [{'url': 'https://example.test/result.png'}]},
            {'data': [{'b64_json': base64.b64encode(JPEG).decode()}]},
            {'data': [{'b64_json': base64.b64encode(PNG).decode()}] * 2},
        )
        for response in malformed:
            with self.subTest(item_count=len(response['data'])), self.assertRaises(ProviderProtocolError):
                OpenAICompatibleImageProvider._validated_response(response, req, native_contract=True)

    def test_26_advanced_options_and_legacy_complexity_fail_before_http(self):
        provider = OpenAICompatibleImageProvider()
        target = RouteTarget('openai_image', 'gpt-image-2.6-flare')
        client, calls = self.transport()
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client', new=client):
            for values in ({'extra_body': {'stream': True}},
                           {'input_fidelity': 'high'}):
                with self.subTest(values=values), self.assertRaises(BackendUnavailable):
                    asyncio.run(provider.generate(ImageRequest(prompt='山谷', **values), target))
            with self.assertRaises(CatalogOperationValidationError):
                asyncio.run(provider.generate(self.request(complexity='high'), self.target()))
        self.assertEqual(calls, [])

    def markup_request(self, model="gpt-image-2.6-flare"):
        source, overlay = markup_fixture()
        (self.uploads / "original.jpg").write_bytes(source)
        (self.uploads / "annotation.png").write_bytes(overlay)
        (self.uploads / "extra.png").write_bytes(PNG)
        return source, overlay, dict(prompt="修改红框里的区域", model=model, image="/uploads/original.jpg",
            reference_images=["/uploads/extra.png"], markup="/uploads/annotation.png")

    def test_markup_confirmation_and_worker_preserve_original_order_and_bytes(self):
        source, overlay, request = self.markup_request()
        prepared = ImageJobAdmissionService().prepare(ImageRequest(**request)).arguments()
        self.assertEqual(prepared["payload"]["request"]["markup"], request["markup"])
        action = self.propose(request=request)
        self.assertEqual(action["review"]["request"]["markup"], request["markup"])
        self.assertTrue(proposal_summary(action)["has_markup"])
        self.assertEqual([item["locator"] for item in action["review"]["references"]],
                         [request["image"], *request["reference_images"], request["markup"]])
        self.assertEqual(self.counts(), (0, 0, 0))
        job = self.job(action)
        client, calls = self.transport()
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client', new=client):
            self.assertEqual(self.runtime().handle(self.message(job["id"]))["status"], "succeeded")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["url"].endswith("/images/edits"))
        self.assertEqual(calls[0]["file_parts"], [
            ("image[]", "image/jpeg", source), ("image[]", "image/png", PNG), ("markup", "image/png", overlay),
        ])
        self.assertEqual(get_generation_by_job_id(job["id"])["status"], "completed")

    def test_markup_content_changed_after_confirmation_never_reaches_provider(self):
        _, _, request = self.markup_request("gpt-image-2.6-sunburst")
        job = self.job(self.propose(request=request))
        # 换成同尺寸且仍有效的标注也必须重新确认。
        with Image.new("RGBA", (12, 8), (0, 0, 0, 0)) as image:
            image.putpixel((0, 0), (0, 0, 255, 255))
            image.save(self.uploads / "annotation.png")
        client, calls = self.transport()
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client', new=client):
            self.assert_rejected(self.runtime(), job)
        self.assertEqual(calls, [])

    def test_markup_rejects_wrong_first_image_and_non_gateway_overlay_at_admission(self):
        _, _, request = self.markup_request()
        for updates, reason in [
            ({"image": None, "reference_images": []}, "source_required"),
            ({"image": "/uploads/extra.png", "reference_images": [request["image"]]}, "size_mismatch"),
            ({"markup": "https://example.test/overlay.png"}, "overlay_unreadable"),
        ]:
            with self.subTest(reason=reason), self.assertRaises(ImageMarkupError) as caught:
                ImageJobAdmissionService().prepare(ImageRequest(**(request | updates)))
            self.assertEqual(caught.exception.code, f"image_markup_{reason}")
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_markup_contract_rejects_mask_animation_generation_and_unverified_targets(self):
        _, _, request = self.markup_request()
        provider = OpenAICompatibleImageProvider()
        client, calls = self.transport()
        test_cases = [
            (RouteTarget("openai_image", request["model"]), {"mask": request["markup"]}),
            (RouteTarget("openai_image", request["model"]), {"output_format": "gif"}),
            (RouteTarget("openai_image", request["model"]), {"operation": "generate"}),
            (self.target(), {}),
            (RouteTarget("openai_image", "unreviewed"), {}),
        ]
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client', new=client):
            for target, values in test_cases:
                with self.subTest(target=target, values=values), self.assertRaises(CatalogOperationValidationError):
                    asyncio.run(provider.generate(ImageRequest(**(request | values)), target))
        with self.assertRaisesRegex(ValueError, "does not declare markup"):
            validate_custom_image_request(ImageRequest(**request), {"capabilities": {"image_edit": True}})
        self.assertEqual(calls, [])

    def test_markup_validates_decoded_content_static_and_oriented_pixel_bounds(self):
        source, overlay = markup_fixture()
        validate_image_markup(source, overlay)
        with BytesIO() as pal_bytes, Image.new("P", (12, 8), 0) as palette:
            palette.putpalette([0, 0, 0, 0, 255, 0])
            palette.putpixel((0, 0), 1)
            palette.save(pal_bytes, "PNG", transparency=0)
            validate_image_markup(source, pal_bytes.getvalue())
        with BytesIO() as apng, Image.new("RGBA", (12, 8), (1, 2, 0, 150)) as frame, Image.new("RGBA", (12, 8)) as frame2:
            frame.save(apng, "PNG", save_all=True, append_images=[frame2], duration=100)
            animated = apng.getvalue()
        with BytesIO() as png, Image.new("1", (4097, 4096), 0) as huge:
            huge.save(png, "PNG")
            oversize = png.getvalue()
        raw_source, webp_markup = markup_fixture(orientation=1, overlay_format="WEBP")
        for main, layer, reason in [
            (source, PNG, "opaque"), (source, webp_markup, "overlay_unreadable"),
            (source, overlay[:30], "overlay_unreadable"), (source, animated, "animated"),
            (oversize, overlay, "size_limit"), (raw_source, overlay, "size_mismatch"),
        ]:
            with self.subTest(reason=reason), self.assertRaises(ImageMarkupError) as caught:
                validate_image_markup(main, layer)
            self.assertEqual(caught.exception.code, f"image_markup_{reason}")

    def test_markup_has_own_hash_slot_and_no_legacy_collision(self):
        _, _, data = self.markup_request()
        routes = {"provider_mode": "builtin", "resolved_chain": [RouteTarget("openai_image", data["model"])]}
        original = ImageRequest(**data)
        markup_hash = build_image_request_hash_payload(original, **routes).payload
        self.assertIn("markup_input", markup_hash)
        for update in ({"markup": "/uploads/new.png"}, {"markup": None, "mask": data["markup"]}):
            self.assertNotEqual(markup_hash, build_image_request_hash_payload(ImageRequest(**(data | update)), **routes).payload)
        self.assertIsNone(build_legacy_image_request_hash_payload_v1(original, **routes).payload)

    def test_26_changing_complexity_after_confirmation_is_rejected_by_worker(self):
        job = self.job(self.propose(request={'model': 'gpt-image-2.6-sunburst', 'prompt': '原始图像', 'complexity': 'medium'}))
        with closing(db_connect()) as conn:
            payload = json.loads(job['input_json'])
            payload['request']['complexity'] = 'high'
            conn.execute('UPDATE jobs SET input_json=? WHERE id=?', (json.dumps(payload), job['id']))
            conn.commit()
        client, calls = self.transport()
        with patch('angemedia_gateway.providers.image.openai_compatible.provider_client', new=client):
            self.assert_rejected(self.runtime(), job)
        self.assertEqual(calls, [])
