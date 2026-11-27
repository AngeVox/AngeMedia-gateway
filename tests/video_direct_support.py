"""即时入口的真实适配器、SQL和来源收据夹具；仅替换远端HTTP，不另造执行服务。"""
import asyncio
from contextlib import closing
from unittest.mock import AsyncMock

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway.adapters.agnes_video import AgnesVideoProvider
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.media import localize_video_result
from angemedia_gateway.repositories.jobs import list_jobs
from angemedia_gateway.repositories.video_job_origins import get_video_job_origin
from angemedia_gateway.schemas import VideoRequest
from angemedia_gateway.services.video_generation import create_video


class DirectVideoFixture(ActionWorkerFixture):
    def provider(self, *, task_id='direct-video-task', polls=None, on_submit=None):
        provider = AgnesVideoProvider('unused', 'https://unused.invalid/v1', poll_interval=0, max_poll_time=1)
        remaining = list(polls or [{'status':'completed','video_url':'https://cdn.example.test/direct.mp4'}])
        calls = []
        async def request(method, url, *, operation, **kwargs):
            calls.append({'operation':operation,'url':url,**kwargs})
            if operation == 'submit':
                job = list_jobs(kind='video',limit=1)[0]
                self.assertIsNotNone(get_video_job_origin(job['id']))
                self.assertIsNone(job['external_task_id'])
                if on_submit: on_submit(job)
                return {'video_id':task_id,'status':'queued'}
            value = remaining.pop(0)
            if isinstance(value, Exception): raise value
            return value
        provider._request_json = AsyncMock(side_effect=request)
        return provider,calls

    def create_direct(self, provider, *, localize=localize_video_result, **values):
        request = VideoRequest(prompt='即时执行', **values)
        return asyncio.run(create_video(request, agnes_video_provider=provider,
            builtin_provider_enabled_func=lambda provider: True, localize_video_result_func=localize))

    def direct_rows(self):
        with closing(db_connect()) as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM jobs WHERE kind='video' ORDER BY created_at")]

    def histories(self):
        with closing(db_connect()) as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM generations ORDER BY created_at')]
