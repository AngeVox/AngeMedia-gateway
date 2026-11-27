"""新进程从数据库恢复执行凭据，不依赖聊天正文或进程内授权缓存。"""
from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

from assistant_action_worker_support import ActionWorkerFixture


def execute_after_restart(path, message, output, crash=False):
    import angemedia_gateway.config as C
    from angemedia_gateway.media_execution_scope import current_execution_scope
    from angemedia_gateway.providers.runtime_config import resolve_provider_runtime_config
    from angemedia_gateway.services.image_execution import ImageExecutionService
    from angemedia_gateway.services.image_job_worker import ImageJobWorker
    from angemedia_gateway.services.job_stage_registry import JobStageRegistry
    from angemedia_gateway.services.worker_runtime import WorkerRuntime
    C.DB_FILE = Path(path); C.OUTPUT_DIR = C.DB_FILE.parent / 'generated'; C.UPLOAD_DIR = C.DB_FILE.parent / 'uploads'
    C.SILICONFLOW_API_KEY = 'fixture-siliconflow-credential-123456789'; C.AGNES_API_KEY = 'fixture-agnes-credential-123456789'
    calls = []
    async def generate(request, target):
        if crash:
            scope = current_execution_scope()
            names = [reference._stream.name for reference in scope._references.values()]
            output.put({'reference_handles': names}); output.close(); output.join_thread()
            os._exit(23)
        calls.append(resolve_provider_runtime_config(target.provider).api_key == C.SILICONFLOW_API_KEY)
        file = C.OUTPUT_DIR / 'restarted.png'; file.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        return {'data': [{'url': C.PUBLIC_BASE_URL + '/generated/restarted.png', 'local_path': str(file)}]}
    async def localize(result, *args, **kwargs): return result
    worker = ImageJobWorker(executor=ImageExecutionService(providers={'siliconflow': SimpleNamespace(generate=generate)}, localize_image_result_func=localize))
    runtime = WorkerRuntime(registry=JobStageRegistry({'image_generate': worker.handle}), runtime_refresher=lambda: None)
    result = runtime.handle(message)
    output.put({'status': result['status'], 'calls': calls, 'scope_released': current_execution_scope() is None})


class ActionExecutionProcessTest(ActionWorkerFixture, unittest.TestCase):
    def test_process_exit_drops_anonymous_reference_handles_without_deleting_source_image(self):
        image = self.uploads / 'frame.png'; image.write_bytes(b'\x89PNG\r\n\x1a\nframe')
        job = self.job(self.propose(request={'prompt': '山谷', 'model': 'kolors', 'image': '/uploads/frame.png'}))
        context = multiprocessing.get_context('spawn'); queue = context.Queue()
        child = context.Process(target=execute_after_restart, args=(str(self.service.db_path), self.message(job['id']), queue, True))
        try:
            child.start(); result = queue.get(timeout=30)
            child.join(10); self.assertEqual(child.exitcode, 23)
            self.assertTrue(result['reference_handles'])
            for name in result['reference_handles']:
                if isinstance(name, str): self.assertFalse(Path(name).exists())
            self.assertEqual(image.read_bytes(), b'\x89PNG\r\n\x1a\nframe')
        finally:
            if child.is_alive(): child.terminate(); child.join(5)
            queue.close(); queue.join_thread()

    def test_cold_worker_executes_archived_action_once_and_next_process_sees_duplicate(self):
        from contextlib import closing
        from angemedia_gateway.db.connection import db_connect
        from angemedia_gateway.repositories.assets import list_assets
        job = self.job(self.propose())
        with closing(db_connect()) as conn:
            conn.execute('DELETE FROM assistant_sessions WHERE id=?', (self.session,))
        context = multiprocessing.get_context('spawn')
        for expected, calls in [('succeeded', [True]), ('duplicate', [])]:
            queue = context.Queue()
            child = context.Process(target=execute_after_restart, args=(str(self.service.db_path), self.message(job['id']), queue))
            try:
                child.start(); result = queue.get(timeout=30)
                child.join(10); self.assertEqual(child.exitcode, 0)
                self.assertEqual(result, {'status': expected, 'calls': calls, 'scope_released': True})
            finally:
                if child.is_alive(): child.terminate(); child.join(5)
                queue.close(); queue.join_thread()
        self.assertEqual(len(list_assets(job_id=job['id'])), 1)


if __name__ == '__main__': unittest.main()
