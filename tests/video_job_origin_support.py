"""普通队列来源测试夹具：使用正式入队、执行服务及持久化事务。"""
from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.schemas import VideoRequest
from angemedia_gateway.services.video_job_admission import VideoJobAdmissionService


class VideoOriginFixture(ActionWorkerFixture):
    def ordinary_job(self, **changes):
        request = {'prompt': '普通队列蓝色山谷', 'model': 'agnes-video-v2.0', 'wait_for_completion': False, **changes}
        return VideoJobAdmissionService().submit(VideoRequest(**request)).job

    def submitted_origin(self, **changes):
        job = self.ordinary_job(**changes)
        runtime = self.video_runtime()
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        return get_job(job['id']), runtime
