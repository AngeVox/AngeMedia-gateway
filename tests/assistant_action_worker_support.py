"""动作执行测试共用正式worker、持久化和可观察的供应商夹具。"""
from __future__ import annotations

from contextlib import closing
import json
from types import SimpleNamespace

from assistant_action_support import ActionFixture
import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.queue.messages import JobStageMessage
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.services.image_execution import ImageExecutionService
from angemedia_gateway.services.image_job_worker import ImageJobWorker
from angemedia_gateway.services.job_stage_registry import JobStageRegistry
from angemedia_gateway.services.video_execution import VideoExecutionService
from angemedia_gateway.services.video_job_worker import VideoJobWorker
from angemedia_gateway.services.video_asset_import import VideoAssetImportResult
from angemedia_gateway.services.worker_runtime import WorkerRuntime


class ActionWorkerFixture(ActionFixture):
    def setUp(self):
        super().setUp()
        self.calls = []

    def job(self, action):
        return get_job(self.confirm(action)['action']['job']['id'])

    def message(self, job_id):
        with closing(db_connect()) as conn:
            dispatch = dict(conn.execute('SELECT * FROM job_dispatches WHERE job_id=? ORDER BY rowid DESC LIMIT 1', (job_id,)).fetchone())
        payload = json.loads(dispatch['payload_json'])
        return JobStageMessage(job_id=job_id, stage=payload['stage'], attempt=payload['attempt'],
            dispatch_id=dispatch['id'], trace_id=dispatch['id']).to_dict()

    def image_result(self):
        output = self.output / 'action.png'
        output.write_bytes(b'\x89PNG\r\n\x1a\nfixture-output')
        return {'data': [{'url': C.PUBLIC_BASE_URL + '/generated/action.png', 'local_path': str(output)}]}

    def image_runtime(self, *, generate=None, providers=None, custom_generate=None):
        async def default_generate(request, target):
            self.calls.append(('image', request.prompt))
            if generate is not None:
                generate(request, target)
            return self.image_result()
        async def localize(result, *args, **kwargs):
            return self.image_result()
        executor = ImageExecutionService(providers=providers or {'siliconflow': SimpleNamespace(generate=default_generate)},
            localize_image_result_func=localize, **({'generate_custom_image_func': custom_generate} if custom_generate else {}))
        worker = ImageJobWorker(executor=executor)
        return WorkerRuntime(registry=JobStageRegistry({'image_generate': worker.handle,
            'image_poll':worker.handle_poll,'image_import':worker.handle_import}), runtime_refresher=lambda: None)

    def video_action(self, *, reference=False):
        request = {'prompt': '蓝色山谷缓慢推进', 'model': 'agnes-video-v2.0'}
        if reference:
            (self.uploads / 'frame.png').write_bytes(b'\x89PNG\r\n\x1a\nframe-a')
            request['image'] = '/uploads/frame.png'
        return self.propose(kind='video', request=request)

    def video_runtime(self, *, on_submit=None, on_poll=None):
        async def submit(request):
            self.calls.append(('submit', request.prompt))
            if on_submit is not None:
                on_submit(request)
            return {'task_id': 'action-video-task', 'status': 'queued'}
        async def poll(task_id, *, model_name=None):
            self.calls.append(('poll', task_id))
            self.poll_models = [*getattr(self, 'poll_models', []), model_name]
            if on_poll is not None:
                on_poll(task_id)
            return {'task_id': task_id, 'status': 'completed', 'video_url': 'https://example.invalid/action.mp4'}
        async def import_completed(task_id, result):
            self.calls.append(('import', task_id))
            output = self.output / 'action.mp4'; output.write_bytes(b'fixture-video')
            return VideoAssetImportResult({'task_id': task_id, 'status': 'completed',
                'video_url': C.PUBLIC_BASE_URL + '/generated/action.mp4', 'local_path': str(output), 'localized': True}, 1)
        worker = VideoJobWorker(executor=VideoExecutionService(provider=SimpleNamespace(submit_task=submit, poll_task=poll)),
            asset_importer=SimpleNamespace(import_completed=import_completed))
        return WorkerRuntime(registry=JobStageRegistry({'video_submit': worker.handle_submit,
            'video_poll': worker.handle_poll, 'asset_import': worker.handle_asset_import}), runtime_refresher=lambda: None)

    def assert_rejected(self, runtime, job):
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'failed')
        current = get_job(job['id'])
        self.assertEqual(current['error_category'], 'action_authorization')
        self.assertFalse(current['retryable'])
        self.assertTrue(current['error_code'].startswith('action_execution_'))
        self.assertIsNone(current['external_task_id'])
        return current
