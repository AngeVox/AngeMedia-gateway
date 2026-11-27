"""已验证文件的 HTTP 传输，沿用 Starlette 的 HEAD 和 Range 行为。"""
from __future__ import annotations

import os
import sys
from contextlib import ExitStack
from mimetypes import guess_type
from pathlib import Path

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse

from .storage_files import storage_directory


class MediaFileResponse(FileResponse):
    def __init__(self, root: Path, parts: tuple[str, ...], *, media_type=None, headers=None):
        self.root = root
        self.parts = parts
        logical_path = root.joinpath(*parts)
        super().__init__(logical_path, media_type=media_type or guess_type(logical_path)[0] or "text/plain",
                         headers=headers)

    @staticmethod
    def _open(stack, root, parts):
        directory = stack.enter_context(storage_directory(root, parts[:-1]))
        return stack.enter_context(directory.read(parts[-1]))

    async def __call__(self, scope, receive, send):
        with ExitStack() as stack:
            try:
                stream = await run_in_threadpool(self._open, stack, self.root, self.parts)
            except (OSError, ValueError):
                raise HTTPException(status_code=404, detail="文件不存在") from None

            # POSIX 从打开的描述符读取。Windows 的原路径由目录和文件句柄固定。
            if os.name != "nt":
                fd_root = "/proc/self/fd" if sys.platform == "linux" else "/dev/fd"
                self.path = f"{fd_root}/{stream.fileno()}"
            self.stat_result = os.fstat(stream.fileno())
            self.set_stat_headers(self.stat_result)
            extensions = scope.get("extensions", {})
            if "http.response.pathsend" in extensions:
                scope = {**scope, "extensions": {key: value for key, value in extensions.items()
                                                  if key != "http.response.pathsend"}}
            await super().__call__(scope, receive, send)
