"""不可变媒体快照经真实worker流水线执行；供应商和下载由本地夹具代替。"""
from __future__ import annotations

from contextlib import closing
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from assistant_run_support import RunFixture
import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.db.readonly_scope import readonly_database
from angemedia_gateway.job_submission import JobSubmissionKey
from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.queue.messages import JobStageMessage
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.schemas import ImageRequest, VideoRequest
from angemedia_gateway.services.image_execution import ImageExecutionResult
from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService
from angemedia_gateway.services.image_job_worker import ImageJobWorker
from angemedia_gateway.services.job_admission import JobAdmissionService
from angemedia_gateway.services.job_stage_registry import JobStageRegistry
from angemedia_gateway.services.video_asset_import import VideoAssetImportResult
from angemedia_gateway.services.video_execution import VideoPollResult, VideoSubmitResult, VideoProviderDisabled
from angemedia_gateway.services.video_job_admission import VideoJobAdmissionService
from angemedia_gateway.services.video_job_worker import VideoJobWorker
from angemedia_gateway.services.worker_runtime import WorkerRuntime


class PreparedMediaAdmissionTest(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        item=patch.object(C, 'SILICONFLOW_API_KEY', 'fixture-prepared-media-key');item.start();self.addCleanup(item.stop)
        self.output = self.temp / 'generated'; self.output.mkdir()
        self.output_patch = patch.object(C, 'OUTPUT_DIR', self.output); self.output_patch.start()

    def tearDown(self):
        self.output_patch.stop(); super().tearDown()

    def image_service(self):
        return ImageJobAdmissionService(resolve_chain_func=lambda _: [RouteTarget('siliconflow', 'Kwai-Kolors/Kolors')])

    def counts(self):
        with closing(db_connect()) as conn:
            return tuple(conn.execute(f'SELECT count(*) FROM {name}').fetchone()[0] for name in ('jobs', 'job_dispatches', 'job_submission_keys'))

    def message(self, dispatch):
        payload = json.loads(dispatch['payload_json'])
        return JobStageMessage(job_id=dispatch['job_id'], stage=payload['stage'], attempt=payload['attempt'],
            dispatch_id=dispatch['id'], trace_id=dispatch['id']).to_dict()

    def next_dispatch(self, job_id):
        with closing(db_connect()) as conn:
            return dict(conn.execute("SELECT * FROM job_dispatches WHERE job_id=? AND status='pending' ORDER BY rowid DESC LIMIT 1", (job_id,)).fetchone())

    def test_prepare_is_readonly_and_freezes_original_request_route_and_hash(self):
        request = ImageRequest(prompt='自然光下的蓝色山谷', model='kolors', size='1024x1024')
        with readonly_database(self.repo.db_path): prepared = self.image_service().prepare(request)
        request.prompt = '红色'; request.size = '512x512'
        arguments = prepared.arguments()
        self.assertEqual(arguments['payload']['request']['prompt'], '自然光下的蓝色山谷')
        self.assertEqual(arguments['payload']['request']['size'], '1024x1024')
        self.assertEqual(arguments['payload']['route']['routes'], [{'provider': 'siliconflow', 'model': 'Kwai-Kolors/Kolors'}])
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_prepare_keeps_existing_validation_and_does_not_accept_model_supplied_submission_key(self):
        with self.assertRaises(ValueError): self.image_service().prepare(ImageRequest(prompt='blue', response_format='b64_json'))
        with self.assertRaises(ValueError): self.image_service().prepare(ImageRequest(prompt='blue', submission_key=uuid4().hex))
        with self.assertRaises(VideoProviderDisabled):
            VideoJobAdmissionService(provider_enabled_func=lambda _: False).prepare(VideoRequest(prompt='gentle pan'))
        with self.assertRaises(ValueError):
            VideoJobAdmissionService(provider_enabled_func=lambda _: True).prepare(VideoRequest(prompt='gentle pan', wait_for_completion=True))
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_image_snapshot_one_admission_one_provider_call_one_asset_after_terminal_replay(self):
        prepared = self.image_service().prepare(ImageRequest(prompt='蓝色山谷', model='kolors'))
        key = JobSubmissionKey('assistant_action', uuid4().hex)
        admitted = JobAdmissionService().admit(**prepared.arguments(), submission_key=key)
        output = self.output / 'blue.png'; output.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        calls = []
        async def execute(request, plan):
            calls.append(request.prompt)
            return ImageExecutionResult({'data': [{'url': C.PUBLIC_BASE_URL + '/generated/blue.png', 'local_path': str(output)}]},
                'siliconflow', 'Kwai-Kolors/Kolors', request.model, 'explicit_model', 1, '2026-10-27T00:00:00+00:00')
        worker = ImageJobWorker(executor=SimpleNamespace(execute=execute))
        runtime = WorkerRuntime(registry=JobStageRegistry({'image_generate': worker.handle}), runtime_refresher=lambda: None)
        self.assertEqual(runtime.handle(self.message(admitted.dispatch))['status'], 'succeeded')
        replay = JobAdmissionService().admit(**prepared.arguments(), submission_key=key)
        self.assertEqual(replay.job['id'], admitted.job['id']); self.assertFalse(replay.created)
        self.assertEqual(runtime.handle(self.message(admitted.dispatch))['status'], 'duplicate')
        self.assertEqual(calls, ['蓝色山谷']); self.assertEqual(len(list_assets(job_id=admitted.job['id'])), 1)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_video_snapshot_keeps_one_provider_submit_through_poll_import_and_replay(self):
        request = VideoRequest(prompt='蓝色山谷缓慢推进', model='agnes-video-v2.0', wait_for_completion=False)
        prepared = VideoJobAdmissionService(provider_enabled_func=lambda _: True).prepare(request)
        key = JobSubmissionKey('assistant_action', uuid4().hex)
        admitted = JobAdmissionService().admit(**prepared.arguments(), submission_key=key)
        calls = []
        async def submit(req):
            calls.append('submit'); return VideoSubmitResult('local-fixture-task', 'queued', 1, '2026-10-27T00:00:00+00:00')
        async def poll(task_id, **kwargs):
            calls.append('poll'); return VideoPollResult(task_id, 'completed', 'https://example.invalid/fixture.mp4')
        async def import_completed(task_id, result):
            calls.append('import')
            output = self.output / 'blue.mp4'; output.write_bytes(b'fixture-video')
            return VideoAssetImportResult({'task_id': task_id, 'status': 'completed', 'video_url': C.PUBLIC_BASE_URL + '/generated/blue.mp4',
                'local_path': str(output), 'localized': True}, 1)
        worker = VideoJobWorker(executor=SimpleNamespace(submit=submit, poll=poll), asset_importer=SimpleNamespace(import_completed=import_completed))
        runtime = WorkerRuntime(registry=JobStageRegistry({'video_submit': worker.handle_submit,
            'video_poll': worker.handle_poll, 'asset_import': worker.handle_asset_import}), runtime_refresher=lambda: None)
        self.assertEqual(runtime.handle(self.message(admitted.dispatch))['status'], 'scheduled')
        self.assertEqual(JobAdmissionService().admit(**prepared.arguments(), submission_key=key).job['id'], admitted.job['id'])
        self.assertEqual(runtime.handle(self.message(self.next_dispatch(admitted.job['id'])))['status'], 'scheduled')
        self.assertEqual(runtime.handle(self.message(self.next_dispatch(admitted.job['id'])))['status'], 'succeeded')
        self.assertEqual(get_job(admitted.job['id'])['status'], 'succeeded')
        self.assertEqual(JobAdmissionService().admit(**prepared.arguments(), submission_key=key).job['status'], 'succeeded')
        self.assertEqual(runtime.handle(self.message(admitted.dispatch))['status'], 'duplicate')
        self.assertEqual(calls, ['submit', 'poll', 'poll', 'import'])
        self.assertEqual(len(list_assets(job_id=admitted.job['id'])), 1)
        self.assertEqual(self.counts(), (1, 3, 1))
