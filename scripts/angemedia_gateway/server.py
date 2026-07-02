"""AngeMedia Gateway FastAPI 应用装配。"""
from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config as C
from .routes import admin, jobs, media, pages, storage
from .routes.media import create_image, create_video, get_video
from .runtime import refresh_runtime, require_auth
from .version import __version__

app = FastAPI(title="AngeMedia Gateway", version=__version__)
if C.FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(C.FRONTEND_DIR / "assets")), name="assets")

app.include_router(pages.router)
app.include_router(admin.router)
app.include_router(jobs.router)
app.include_router(media.router)
app.include_router(storage.router)


if __name__ == "__main__":
    uvicorn.run(app, host=C.HOST, port=C.PORT)
