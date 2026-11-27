"""独立进程读取队列任务，不能发起第二条查询/下载流水线或改写状态。"""
import asyncio
from contextlib import closing
import multiprocessing
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.jobs import get_job
from test_assistant_action_processes import _service


def reader_child(path,barrier,output):
    _service(path)
    from angemedia_gateway.services.video_generation import get_video
    async def forbidden(*args,**kwargs):raise AssertionError('读取不应发起外部调用或下载')
    if barrier is not None:barrier.wait(20)
    rows=[asyncio.run(get_video('action-video-task',agnes_video_provider=SimpleNamespace(poll_task=forbidden),localize_video_result_func=forbidden)) for _ in range(3)]
    output.put(rows)


def worker_child(path,job_id,barrier,output):
    _service(path)
    import angemedia_gateway.config as C
    helper=ActionWorkerFixture();helper.calls=[];helper.output=C.OUTPUT_DIR
    runtime=helper.video_runtime();barrier.wait(20)
    for _ in range(2):runtime.handle(helper.message(job_id))
    output.put(helper.calls)


class VideoProviderStateProcessesTest(ActionWorkerFixture,unittest.TestCase):
    def setUp(self):
        super().setUp();self.context=multiprocessing.get_context('spawn');self.children=[];self.queues=[]

    def tearDown(self):
        for child in self.children:
            child.join(2)
            if child.is_alive():child.terminate();child.join(5)
        for queue in self.queues:queue.close();queue.join_thread()
        super().tearDown()

    def queue(self):
        value=self.context.Queue();self.queues.append(value);return value

    def spawn(self,function,*args):
        child=self.context.Process(target=function,args=(str(self.service.db_path),*args));self.children.append(child);child.start();return child

    def ready(self):
        job=self.job(self.video_action());runtime=self.video_runtime();runtime.handle(self.message(job['id']))
        return get_job(job['id']),runtime

    def test_cold_read_of_local_failure_preserves_provider_record_and_job(self):
        job,runtime=self.ready();worker=runtime.registry.get('video_poll').__self__
        with patch.object(type(worker.policy),'exhausted',return_value=True):runtime.handle(self.message(job['id']))
        before=get_job(job['id'])
        with closing(db_connect()) as conn:task=dict(conn.execute('SELECT * FROM video_tasks').fetchone())
        output=self.queue();child=self.spawn(reader_child,None,output);rows=output.get(timeout=30)
        child.join(10);self.assertEqual(child.exitcode,0)
        self.assertTrue(all(row['status']=='queued' and row['job_status']=='failed' for row in rows))
        self.assertEqual(get_job(job['id']),before)
        with closing(db_connect()) as conn:self.assertEqual(dict(conn.execute('SELECT * FROM video_tasks').fetchone()),task)

    def test_parallel_compatibility_readers_cannot_interfere_with_worker_finalization(self):
        job,runtime=self.ready();barrier=self.context.Barrier(3);reads=self.queue();work=self.queue()
        children=[self.spawn(reader_child,barrier,reads) for _ in range(2)]
        children.append(self.spawn(worker_child,job['id'],barrier,work))
        observations=[reads.get(timeout=30) for _ in range(2)];calls=work.get(timeout=30)
        for child in children:child.join(10);self.assertEqual(child.exitcode,0)
        for rows in observations:
            for row in rows:
                self.assertTrue(row['worker_managed']);self.assertIn(row['job_status'],{'running','succeeded'})
                self.assertNotIn('local_path',row)
        self.assertEqual([row[0] for row in calls],['poll','poll','import'])
        self.assertEqual(get_job(job['id'])['status'],'succeeded');self.assertEqual(self.counts(),(1,3,1))
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM assets WHERE job_id=?',(job['id'],)).fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT status FROM video_tasks').fetchone()[0],'completed')
