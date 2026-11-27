"""视频已提交后的轮询和导入只核验原连接；不会因助手停用而重新生成。"""
from __future__ import annotations

import base64
from contextlib import closing
import unittest
from unittest.mock import AsyncMock, patch

from assistant_action_worker_support import ActionWorkerFixture
import angemedia_gateway.config as C
from angemedia_gateway.adapters.agnes_video import AgnesVideoProvider
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.media_execution_scope import current_execution_scope, require_execution_target
from angemedia_gateway.providers.runtime_config import resolve_provider_runtime_config, resolve_live_provider_runtime_config
from angemedia_gateway.providers.image.openai_compatible import openai_image_file_part
from angemedia_gateway.reference_images import materialize_gateway_image_reference
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.repositories.settings import set_config
from angemedia_gateway.services.job_stage_registry import JobStageRegistry
from angemedia_gateway.services.video_execution import VideoExecutionService
from angemedia_gateway.services.video_job_worker import VideoJobWorker
from angemedia_gateway.services.worker_runtime import WorkerRuntime


class ActionVideoExecutionTest(ActionWorkerFixture, unittest.TestCase):
    def test_approved_video_keeps_one_submit_through_poll_import_and_terminal_replay(self):
        action = self.video_action(); job = self.job(action); runtime = self.video_runtime()
        first = self.message(job['id'])
        for expected in ('scheduled', 'scheduled', 'succeeded'):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'], expected)
        self.assertEqual([item[0] for item in self.calls], ['submit', 'poll', 'poll', 'import'])
        self.assertEqual(len(list_assets(job_id=job['id'])), 1)
        self.assertFalse(self.confirm(action)['created'])
        self.assertEqual(runtime.handle(first)['status'], 'duplicate')

    def test_policy_stop_and_deleted_reference_do_not_cancel_existing_video_collection(self):
        job = self.job(self.video_action(reference=True)); runtime = self.video_runtime()
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        set_config('ANGE_ASSISTANT_ENABLED', 'false'); set_config('ANGE_ASSISTANT_ALLOW_PAID', 'false')
        (self.uploads / 'frame.png').unlink()
        with closing(db_connect()) as conn:
            conn.execute('DELETE FROM assistant_sessions WHERE id=?', (self.session,))
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'succeeded')
        self.assertEqual([item[0] for item in self.calls], ['submit', 'poll', 'poll', 'import'])

    def test_credential_rotation_after_submit_does_not_query_another_account(self):
        job = self.job(self.video_action()); runtime = self.video_runtime()
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        update_provider_runtime_config('agnes_video', api_key='fixture-other-account-123456789')
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'failed')
        current = get_job(job['id'])
        self.assertEqual(current['error_code'], 'action_execution_connection_changed')
        self.assertEqual(current['external_task_id'], 'action-video-task')
        self.assertFalse(current['retryable']); self.assertEqual(len(self.calls), 1)

    def test_connection_change_during_import_stage_fails_without_poll_or_automatic_retry(self):
        job = self.job(self.video_action()); runtime = self.video_runtime()
        for _ in range(2): self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        update_provider_runtime_config('agnes_video', base_url_override='https://changed.invalid/v1')
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual([item[0] for item in self.calls], ['submit', 'poll'])
        self.assertFalse(get_job(job['id'])['retryable'])

    def test_changed_reference_before_submit_is_rejected_without_provider_call(self):
        job = self.job(self.video_action(reference=True))
        (self.uploads / 'frame.png').write_bytes(b'\x89PNG\r\n\x1a\nframe-b')
        self.assert_rejected(self.video_runtime(), job); self.assertEqual(self.calls, [])

    def test_change_between_reference_hash_and_capture_is_rejected(self):
        from angemedia_gateway.services import assistant_action_execution as module
        job = self.job(self.video_action(reference=True)); capture = module._freeze_references
        def change(references, resources):
            (self.uploads / 'frame.png').write_bytes(b'\x89PNG\r\n\x1a\nframe-b')
            return capture(references, resources)
        with patch.object(module, '_freeze_references', side_effect=change):
            self.assert_rejected(self.video_runtime(), job)
        self.assertEqual(self.calls, [])

    def test_after_final_gate_reference_and_runtime_reads_stay_on_approved_snapshot(self):
        job = self.job(self.video_action(reference=True)); before = resolve_live_provider_runtime_config('agnes_video')
        seen = []
        def submit(request):
            update_provider_runtime_config('agnes_video', api_key='fixture-other-account-123456789', base_url_override='https://changed.invalid/v1')
            (self.uploads / 'frame.png').write_bytes(b'\x89PNG\r\n\x1a\nframe-b')
            runtime = resolve_provider_runtime_config('agnes_video')
            content = base64.b64decode(materialize_gateway_image_reference(request.image).partition(',')[2])
            seen.append((runtime.api_key, runtime.base_url, content, openai_image_file_part(request.image, 1)[1]))
        self.assertEqual(self.video_runtime(on_submit=submit).handle(self.message(job['id']))['status'], 'scheduled')
        self.assertEqual(seen, [(before.api_key, before.base_url, b'\x89PNG\r\n\x1a\nframe-a', b'\x89PNG\r\n\x1a\nframe-a')])
        self.assertEqual(resolve_provider_runtime_config('agnes_video').api_key, 'fixture-other-account-123456789')
        self.assertIsNone(current_execution_scope())
        self.assertEqual(base64.b64decode(materialize_gateway_image_reference('/uploads/frame.png').partition(',')[2]), b'\x89PNG\r\n\x1a\nframe-b')

    def test_poll_scope_cannot_be_reused_to_authorize_another_submit(self):
        job = self.job(self.video_action())
        def wrong_operation(task_id):
            require_execution_target('agnes_video', job['model'])
        runtime = self.video_runtime(on_poll=wrong_operation)
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual(get_job(job['id'])['error_code'], 'action_execution_target_changed')
        self.assertFalse(get_job(job['id'])['retryable'])

    def test_real_agnes_adapter_builds_approved_bytes_and_headers_after_late_changes(self):
        job = self.job(self.video_action(reference=True)); before = resolve_live_provider_runtime_config('agnes_video')
        provider = AgnesVideoProvider('unused', 'https://unused.invalid/v1')
        prepare = provider._prepare_runtime_request
        async def change(request):
            update_provider_runtime_config('agnes_video', api_key='fixture-other-account-123456789', base_url_override='https://changed.invalid/v1')
            (self.uploads / 'frame.png').write_bytes(b'\x89PNG\r\n\x1a\nframe-b')
            return await prepare(request)
        worker = VideoJobWorker(executor=VideoExecutionService(provider=provider))
        runtime = WorkerRuntime(registry=JobStageRegistry({'video_submit': worker.handle_submit}), runtime_refresher=lambda: None)
        with patch.object(provider, '_prepare_runtime_request', side_effect=change), patch.object(provider, '_request_json',
                new=AsyncMock(return_value={'task_id': 'fixture-real-adapter-task', 'status': 'queued'})) as request:
            self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        self.assertEqual(request.call_args.args[1], before.base_url + '/videos')
        self.assertEqual(request.call_args.kwargs['headers']['Authorization'], 'Bearer ' + before.api_key)
        self.assertEqual(base64.b64decode(request.call_args.kwargs['json']['image']), b'\x89PNG\r\n\x1a\nframe-a')


if __name__ == '__main__': unittest.main()
