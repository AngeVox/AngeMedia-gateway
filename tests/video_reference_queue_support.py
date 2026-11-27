"""正式视频适配器和worker，共用受控中转HTTP；不替换入队、确认或持久化。"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
import hashlib
from pathlib import Path
import httpx

from video_job_origin_support import VideoOriginFixture
from angemedia_gateway.adapters.agnes_video import AgnesVideoProvider
from angemedia_gateway.repositories.reference_relay_config import update_reference_relay_config
from angemedia_gateway.services.video_execution import VideoExecutionService
from angemedia_gateway.services.video_asset_import import VideoAssetImportResult

FLASH = 'agnes-video-2.5-flash'
PAID = 'agnes-video-2.5'
IMAGE = '/uploads/relay-frame.png'
CONTENT = b'\x89PNG\r\n\x1a\ncontrolled-local-frame'
TOKEN = 'fixture-relay-token-123456789'
RELAY = 'https://relay.invalid/upload'
PUBLIC = 'https://cdn.example.test/published-frame.png'


class ReferenceQueueFixture(VideoOriginFixture):
    def configure_relay(self):
        (self.uploads/'relay-frame.png').write_bytes(CONTENT)
        update_reference_relay_config(mode='external_http', upload_url=RELAY, token=TOKEN)

    def modern_job(self, *, assistant=False, model=FLASH, **changes):
        request = {'prompt': '缓慢推进山谷', 'model': model, 'images': [IMAGE], **changes}
        if assistant:
            action = self.propose(kind='video', request=request)
            return self.job(action)
        return self.ordinary_job(**request)

    def real_adapter_runtime(self, *, task_id='relay-video-task', on_prepare=None, relay_response=None):
        runtime = self.video_runtime()
        provider = AgnesVideoProvider('unused', 'https://unused.invalid/v1')
        prepare = provider._prepare_runtime_request
        if on_prepare:
            async def change(request):
                on_prepare(request)
                return await prepare(request)
            provider._prepare_runtime_request = change
        request = AsyncMock(side_effect=[{'video_id': task_id, 'status': 'queued'},
            {'status': 'completed', 'video_url': 'https://example.invalid/complete.mp4'},
            {'status': 'completed', 'video_url': 'https://example.invalid/complete.mp4'}])
        self.enterContext(patch.object(provider, '_request_json', new=request))
        uploads = []
        def relay_http(message):
            uploads.append((str(message.url), message.headers.get('Authorization'), message.content))
            return relay_response(message) if relay_response else httpx.Response(200, json={'url': PUBLIC})
        @asynccontextmanager
        async def client(**kwargs):
            async with httpx.AsyncClient(transport=httpx.MockTransport(relay_http)) as connection:
                yield connection
        self.enterContext(patch('angemedia_gateway.providers.reference_relay.outbound_client', new=client))
        worker = runtime.registry.get('video_submit').__self__
        worker.executor = VideoExecutionService(provider=provider)
        import_original = worker.asset_importer.import_completed
        async def import_unique(task, result):
            imported = await import_original(task, result)
            path = self.output/(hashlib.sha256(task.encode()).hexdigest()[:16]+'.mp4')
            path.write_bytes(Path(imported.result['local_path']).read_bytes())
            return VideoAssetImportResult({**imported.result, 'local_path': str(path),
                                           'video_url': '/generated/'+path.name}, imported.duration_ms)
        worker.asset_importer.import_completed = import_unique
        return runtime, request, uploads
