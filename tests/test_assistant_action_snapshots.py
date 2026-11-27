"""作用域并发、适配器传输策略和relay快照；异常不能遗留跨任务授权。"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import threading
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway.media_execution_scope import current_execution_scope, require_execution_target
from angemedia_gateway.providers.custom import generate_custom_openai_image
from angemedia_gateway.providers.transport_config import resolve_saved_provider_transport
from angemedia_gateway.reference_images import materialize_gateway_image_reference
from angemedia_gateway.repositories.provider_transport_config import update_provider_transport_config
from angemedia_gateway.repositories.reference_relay_config import update_reference_relay_config, get_reference_relay_config
from angemedia_gateway.repositories.settings import upsert_custom_provider, update_custom_provider_details


class ActionSnapshotTest(ActionWorkerFixture, unittest.TestCase):
    def relay_video_action(self):
        """现代视频确实只收公网URL，用它保留原中转授权覆盖。"""
        (self.uploads / 'frame.png').write_bytes(b'\x89PNG\r\n\x1a\nframe')
        return self.propose(kind='video', request={'prompt':'山谷','model':'agnes-video-2.5','image':'/uploads/frame.png'})

    def test_reference_handles_close_on_success_provider_error_and_validation_error(self):
        from angemedia_gateway.providers.errors import BackendUnavailable
        from angemedia_gateway.services import assistant_action_execution as module
        original = tempfile.TemporaryFile
        for mode in ('success', 'provider_error', 'validation_error'):
            with self.subTest(mode=mode):
                job = self.job(self.video_action(reference=True)); files = []
                def capture(*args, **kwargs):
                    stream = original(*args, **kwargs); files.append(stream); return stream
                def submit(request):
                    if mode == 'provider_error': raise BackendUnavailable('fixture failure')
                with patch('angemedia_gateway.reference_images.tempfile.TemporaryFile', side_effect=capture):
                    if mode == 'validation_error':
                        with patch.object(module, '_capture_connections', side_effect=RuntimeError('fixture-private-validation-error')):
                            self.assert_rejected(self.video_runtime(on_submit=submit), job)
                    else:
                        self.assertEqual(self.video_runtime(on_submit=submit).handle(self.message(job['id']))['status'],
                                         'scheduled' if mode == 'success' else 'failed')
                self.assertTrue(files); self.assertTrue(all(stream.closed for stream in files))
                self.assertIsNone(current_execution_scope())

    def test_required_relay_missing_is_not_presented_as_executable(self):
        from angemedia_gateway.services.assistant_action_policy import action_policy
        policy,_ = action_policy({'kind':'video','provider':'agnes_video','model':'agnes-video-2.5','payload':{}},
                                 [{'kind':'gateway_image'}])
        self.assertFalse(policy['allowed'])
        self.assertIn('reference_relay_unavailable', policy['blocked_reasons'])

    def test_proxy_route_change_after_enqueue_rejects_generation(self):
        job = self.job(self.propose())
        update_provider_transport_config('siliconflow', transport_mode='explicit_proxy', proxy_url='http://user:private@proxy.invalid:8080')
        self.assert_rejected(self.image_runtime(), job); self.assertEqual(self.calls, [])

    def test_custom_adapter_uses_approved_endpoint_key_model_and_proxy_snapshot(self):
        upsert_custom_provider({'id': 'action-custom', 'base_url': 'https://original.invalid/v1', 'api_key': 'fixture-custom-123456789', 'default_model': 'model-a'})
        job = self.job(self.propose(request={'prompt': '山谷', 'model': 'custom:action-custom'})); seen = []
        async def generate(request, provider):
            update_custom_provider_details('action-custom', {'base_url': 'https://changed.invalid/v1', 'api_key': 'fixture-other-123456789', 'default_model': 'model-b'})
            update_provider_transport_config('action-custom', transport_mode='explicit_proxy', proxy_url='http://private@proxy.invalid:8080')
            seen.append(resolve_saved_provider_transport('action-custom').mode)
            return await generate_custom_openai_image(request, provider)
        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        with patch('angemedia_gateway.providers.custom.provider_client', return_value=Client()), patch(
                'angemedia_gateway.providers.custom.request_with_provider_errors', new=AsyncMock(return_value=httpx.Response(200,
                json={'data': [{'url': 'https://example.invalid/output.png'}]}))) as request:
            self.assertEqual(self.image_runtime(custom_generate=generate).handle(self.message(job['id']))['status'], 'succeeded')
        self.assertEqual(seen, ['direct'])
        self.assertEqual(request.call_args.args[2], 'https://original.invalid/v1/images/generations')
        self.assertEqual(request.call_args.kwargs['headers']['Authorization'], 'Bearer fixture-custom-123456789')
        self.assertEqual(request.call_args.kwargs['json']['model'], 'model-a')

    def test_two_threads_keep_reference_contexts_separate_and_release_them(self):
        jobs = []
        for name in ('a', 'b'):
            (self.uploads / (name + '.png')).write_bytes(b'\x89PNG\r\n\x1a\n' + name.encode())
            jobs.append(self.job(self.propose(request={'prompt': name, 'model': 'kolors', 'image': '/uploads/' + name + '.png'})))
        barrier = threading.Barrier(2); seen = []; scopes = []
        def generate(request, target):
            scopes.append(current_execution_scope())
            barrier.wait(10)
            seen.append(base64.b64decode(materialize_gateway_image_reference(request.image).partition(',')[2]))
        runtime = self.image_runtime(generate=generate)
        def run(job):
            result = runtime.handle(self.message(job['id']))
            self.assertIsNone(current_execution_scope())
            return result['status']
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(run, jobs)), ['succeeded', 'succeeded'])
        self.assertEqual(set(seen), {b'\x89PNG\r\n\x1a\na', b'\x89PNG\r\n\x1a\nb'})
        self.assertIsNot(scopes[0], scopes[1])
        self.assertTrue(all(scope._closed and not scope._references and not scope._runtimes for scope in scopes))

    def test_unapproved_target_or_reference_fails_without_leaking_private_exception(self):
        for mode in ('target', 'reference', 'validation'):
            with self.subTest(mode=mode):
                job = self.job(self.propose())
                def generate(request, target):
                    if mode == 'target': require_execution_target('agnes_video', 'unapproved')
                    elif mode == 'reference': materialize_gateway_image_reference('/uploads/not-approved.png')
                    else:
                        def fail(): raise RuntimeError('fixture-private-credential-in-error')
                        current_execution_scope()._validate = fail
                        require_execution_target(target.provider, target.model)
                current = self.assert_rejected(self.image_runtime(generate=generate), job)
                self.assertNotIn('fixture-private-credential', current['error_message'])
                self.assertIsNone(current_execution_scope())

    def test_relay_change_invalidates_approved_local_reference_route(self):
        update_reference_relay_config(mode='external_http', upload_url='https://relay.invalid/upload', token='fixture-relay-123456789')
        job = self.job(self.relay_video_action())
        update_reference_relay_config(upload_url='https://changed-relay.invalid/upload')
        self.assert_rejected(self.video_runtime(), job)
        self.assertEqual(self.calls, [])

    def test_relay_getter_stays_on_approved_snapshot_after_call_starts(self):
        update_reference_relay_config(mode='external_http', upload_url='https://relay.invalid/upload', token='fixture-relay-123456789')
        seen = []
        def submit(request):
            from angemedia_gateway.providers.reference_relay import _reference_file_part
            update_reference_relay_config(upload_url='https://changed-relay.invalid/upload', token='fixture-relay-rotated-123456789')
            (self.uploads / 'frame.png').write_bytes(b'\x89PNG\r\n\x1a\nchanged-frame')
            seen.append({**get_reference_relay_config(), 'content': _reference_file_part(request.image)[1]})
        job = self.job(self.relay_video_action())
        self.assertEqual(self.video_runtime(on_submit=submit).handle(self.message(job['id']))['status'], 'scheduled')
        self.assertEqual(seen[0]['upload_url'], 'https://relay.invalid/upload')
        self.assertEqual(seen[0]['token'], 'fixture-relay-123456789')
        self.assertEqual(seen[0]['content'], b'\x89PNG\r\n\x1a\nframe')
        self.assertEqual(get_reference_relay_config()['upload_url'], 'https://changed-relay.invalid/upload')


if __name__ == '__main__': unittest.main()
