"""独立进程冷读取及并发匹配变化，始终遵守无外查和不猜选边界。"""
import asyncio
import multiprocessing
import unittest
from types import SimpleNamespace

from test_video_task_lookup import TaskLookupFixture
from test_assistant_action_processes import _service
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.jobs import AmbiguousExternalTaskIdError, cleanup_jobs, create_job, get_job
from angemedia_gateway.services.video_generation import get_video
from angemedia_gateway.services.video_job_refresh import VideoJobRefreshError


def read_child(path,barrier,output):
    _service(path)
    async def forbidden(*args,**kwargs):raise AssertionError('不应向当前账户外查或下载')
    if barrier is not None:barrier.wait(20)
    results=[]
    for _ in range(3):
        try:
            result=asyncio.run(get_video('action-video-task',agnes_video_provider=SimpleNamespace(poll_task=forbidden),localize_video_result_func=forbidden))
            results.append('cached' if result.get('record_only') else 'worker' if result.get('worker_managed') else 'unexpected')
        except AmbiguousExternalTaskIdError:results.append('ambiguous')
        except VideoJobRefreshError as error:results.append(error.status_code)
    output.put(results)


def create_child(path,barrier,output):
    _service(path);barrier.wait(20)
    job=create_job(kind='video',status='running',provider='agnes_video',model='agnes-video-v2.0',external_task_id='action-video-task')
    output.put(job['id'])


class VideoTaskLookupProcessesTest(TaskLookupFixture,unittest.TestCase):
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
        return job,runtime

    def test_cleaned_job_is_still_unavailable_after_process_restart_without_losing_asset(self):
        job,runtime=self.ready()
        for _ in range(2):runtime.handle(self.message(job['id']))
        asset=list_assets(job_id=job['id'])[0];cleanup_jobs(job_ids=[job['id']])
        output=self.queue();child=self.spawn(read_child,None,output);self.assertEqual(output.get(timeout=30),['cached']*3)
        child.join(10);self.assertEqual(child.exitcode,0);self.assertEqual(list_assets()[0]['id'],asset['id'])

    def test_concurrent_second_match_is_either_worker_observation_or_explicit_ambiguity(self):
        job,runtime=self.ready();before=get_job(job['id']);barrier=self.context.Barrier(2);reads=self.queue();writes=self.queue()
        children=[self.spawn(read_child,barrier,reads),self.spawn(create_child,barrier,writes)]
        rows=reads.get(timeout=30);new_id=writes.get(timeout=30)
        for child in children:child.join(10);self.assertEqual(child.exitcode,0)
        self.assertTrue(all(row in {'worker','ambiguous'} for row in rows));self.assertEqual(get_job(job['id']),before)
        self.assertEqual(get_job(new_id)['status'],'running')
