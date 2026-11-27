"""实际适配器验证旧即时API合同；共享参考图校验不能顺带改变同步返回或扩展字段。"""
import asyncio
import base64
from contextlib import asynccontextmanager
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from video_reference_queue_support import ReferenceQueueFixture, FLASH, PAID, CONTENT, PUBLIC
from angemedia_gateway.adapters.agnes_video import AgnesVideoProvider
from angemedia_gateway.routes import media as media_routes, admin as admin_routes
from angemedia_gateway.schemas import VideoRequest
from angemedia_gateway.services.video_generation import create_video


class VideoReferenceCompatibilityTest(ReferenceQueueFixture, unittest.TestCase):
    def run_api(self, req):
        provider = AgnesVideoProvider('fixture-api-key', 'https://agnes.invalid/v1', poll_interval=0, max_poll_time=1)
        request = AsyncMock(side_effect=[{'video_id': 'compat-'+req.model, 'status': 'queued'},
                                        {'status': 'completed', 'video_url': 'https://cdn.example.test/output.mp4'}])
        with patch.object(provider, '_request_json', new=request):
            result = asyncio.run(create_video(req, agnes_video_provider=provider,
                builtin_provider_enabled_func=lambda provider: True,
                localize_video_result_func=AsyncMock(side_effect=lambda value: dict(value))))
        return result, request

    def test_actual_post_routes_are_one_public_and_one_admin_route(self):
        app = FastAPI()
        app.include_router(media_routes.router); app.include_router(admin_routes.router)
        paths = [path for path, operations in app.openapi()['paths'].items()
                 if 'post' in operations and path.endswith('videos')]
        self.assertEqual(sorted(paths), ['/v1/admin/jobs/videos', '/v1/videos'])

    def test_v2_immediate_extra_body_keeps_task_history_and_parameter_contract(self):
        result, request = self.run_api(VideoRequest(prompt='兼容扩展', extra_body={'motion': 'pan'}))
        self.assertIn('job_id', result); self.assertIn('history_id', result)
        self.assertEqual(result['task_id'], 'compat-agnes-video-v2.0')
        self.assertEqual(result['status'], 'queued'); self.assertEqual(request.await_count, 1)
        self.assertEqual(request.call_args.kwargs['json']['extra_body']['motion'], 'pan')

    def test_sync_wait_keeps_remote_result_and_history_without_exposing_job_id(self):
        for model in ('agnes-video-v2.0', PAID, FLASH):
            with self.subTest(model=model):
                result, request = self.run_api(VideoRequest(prompt=model, model=model, wait_for_completion=True))
                self.assertNotIn('job_id', result); self.assertIn('history_id', result)
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(result['video_url'], 'https://cdn.example.test/output.mp4')
                self.assertEqual(request.await_count, 2)

    def test_public_url_and_multimodal_roles_remain_native_without_relay(self):
        req = VideoRequest(prompt='混合媒体', model=PAID, images=[PUBLIC],
                           audios=['https://cdn.example.test/audio.mp3'],
                           videos=[{'url': 'https://cdn.example.test/video.mp4', 'start_seconds': 1, 'require_audio': True}])
        result, request = self.run_api(req)
        body = request.call_args.kwargs['json']
        self.assertEqual(body['images'], [PUBLIC]); self.assertEqual(body['audios'], req.audios)
        self.assertEqual(body['videos'], [item.model_dump() for item in req.videos])

    def test_safe_inline_data_url_remains_api_only_and_never_enters_persistent_queue(self):
        self.configure_relay()
        data_url = 'data:image/png;base64,' + base64.b64encode(CONTENT).decode()
        uploads = []
        def upload(message):
            uploads.append(message.content)
            return httpx.Response(200, json={'url': PUBLIC})
        @asynccontextmanager
        async def client(**kwargs):
            async with httpx.AsyncClient(transport=httpx.MockTransport(upload)) as connection:
                yield connection
        with patch('angemedia_gateway.providers.reference_relay.outbound_client', new=client):
            result, request = self.run_api(VideoRequest(prompt='即时内联图', model=FLASH, images=[data_url]))
        self.assertEqual(request.call_args.kwargs['json']['images'], [PUBLIC])
        self.assertIn(CONTENT, uploads[0])
        with self.assertRaises(ValueError): self.modern_job(images=[data_url])

    def test_queue_accepts_local_relay_images_but_does_not_silently_accept_legacy_extra_body(self):
        self.configure_relay(); job = self.modern_job()
        self.assertEqual(job['stage'], 'video_submit')
        with self.assertRaises(ValueError): self.ordinary_job(extra_body={'motion': 'pan'})
