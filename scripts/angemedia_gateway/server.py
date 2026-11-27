"""AngeMedia Gateway FastAPI 应用装配。"""
from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config as C
from .routes import admin, assistant_conversations, assistant_runs, assistant_actions, jobs, media, pages, storage
from .routes import admin_legacy_reviews, assistant_compat
from .services.assistant_lifecycle import assistant_lifespan
from .routes.media import create_image, create_video, get_video
from .runtime import refresh_runtime, require_auth
from .version import __version__
from .services.studio_assets import StudioAssets, studio_assets
from pathlib import Path


class RevalidatingStaticFiles(StaticFiles):
    """版本路径固定整套资源；未版本化路径使用内容ETag重新校验。"""

    def __init__(self, *, directory, snapshot=None):
        super().__init__(directory=directory)
        self.snapshot = snapshot or StudioAssets(Path(directory), Path(directory).parent/'index.html')

    async def get_response(self, path, scope):
        return self.snapshot.asset(path, scope)


app = FastAPI(title="AngeMedia Gateway", version=__version__, lifespan=assistant_lifespan)
if C.FRONTEND_DIR.exists():
    app.mount("/assets", RevalidatingStaticFiles(directory=str(C.FRONTEND_DIR / "assets"), snapshot=studio_assets()), name="assets")

app.include_router(pages.router)
app.include_router(admin.router)
app.include_router(jobs.router)
app.include_router(admin_legacy_reviews.router)
app.include_router(media.router)
app.include_router(assistant_compat.router)
app.include_router(assistant_conversations.router)
app.include_router(assistant_runs.router)
app.include_router(assistant_actions.router)
app.include_router(storage.router)


if __name__ == "__main__":
    uvicorn.run(app, host=C.HOST, port=C.PORT)
