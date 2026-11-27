"""固定一份前端字节与内容版本，避免部署期间的跨版本模块混用。"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import mimetypes
from pathlib import Path

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .. import config as C


class StudioAssets:
    def __init__(self, directory: Path, index: Path):
        self.index = index.read_bytes() if index.is_file() and index.resolve().is_relative_to(index.parent.resolve()) else None
        self.files = {path.relative_to(directory).as_posix(): path.read_bytes()
                      for path in sorted(directory.rglob('*')) if path.is_file() and path.resolve().is_relative_to(directory.resolve())}
        digests = {name: hashlib.sha256(data).hexdigest() for name, data in self.files.items()}
        if self.index is not None:
            digests['/index.html'] = hashlib.sha256(self.index).hexdigest()
        encoded = json.dumps(digests, sort_keys=True, separators=(',', ':')).encode()
        self.revision = 'r-' + hashlib.sha256(encoded).hexdigest()[:24]
        self.etags = {name: '"' + digest + '"' for name, digest in digests.items()}

    def page(self) -> HTMLResponse:
        if self.index is None:
            raise HTTPException(status_code=404, detail='前端文件不存在')
        body = self.index.replace(b'/assets/', ('/assets/' + self.revision + '/').encode())
        return HTMLResponse(body, headers={'Cache-Control': 'no-store', 'X-Studio-Revision': self.revision})

    def asset(self, path: str, scope: dict) -> Response:
        path = Path(path).as_posix()
        if scope['method'] not in ('GET', 'HEAD'):
            return Response(status_code=405, headers={'Allow': 'GET, HEAD', 'Cache-Control': 'no-store'})
        versioned = path.startswith('r-') and '/' in path
        if versioned:
            revision, path = path.split('/', 1)
            if revision != self.revision:
                # 仅未启动的新文档入口转向整套新图，不能把旧依赖单独换成新模块。
                if path == 'studio/app.js':
                    return RedirectResponse('/assets/' + self.revision + '/' + path, headers={'Cache-Control': 'no-store'})
                return JSONResponse({'detail': {'code': 'studio_upgrade_required'}}, status_code=409,
                                    headers={'Cache-Control': 'no-store'})
        data = self.files.get(path)
        if data is None:
            return Response(status_code=404, headers={'Cache-Control': 'no-store'})
        headers = {'Cache-Control': 'public, max-age=31536000, immutable' if versioned else 'no-cache',
                   'ETag': self.etags[path], 'X-Studio-Revision': self.revision}
        candidates = Headers(scope=scope).get('if-none-match', '').split(',')
        if any(value.strip().removeprefix('W/') in ('*', headers['ETag']) for value in candidates):
            return Response(status_code=304, headers=headers)
        media_type = {'.js': 'text/javascript', '.css': 'text/css'}.get(Path(path).suffix)
        headers['Content-Length'] = str(len(data))
        return Response(b'' if scope['method'] == 'HEAD' else data,
                        media_type=media_type or mimetypes.guess_type(path)[0] or 'application/octet-stream', headers=headers)


@lru_cache(maxsize=4)
def _snapshot(root: Path) -> StudioAssets:
    return StudioAssets(root/'assets', root/'index.html')


def studio_assets() -> StudioAssets:
    return _snapshot(Path(C.FRONTEND_DIR).resolve())


def check_studio_revision(revision: str | None) -> None:
    if revision is not None and revision != studio_assets().revision:
        raise HTTPException(status_code=409, detail={'code': 'studio_upgrade_required'})
