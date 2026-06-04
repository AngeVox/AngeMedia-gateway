"""AngeMedia Gateway FastAPI 应用装配。"""
from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config as C
from .routes import admin, media, pages, storage
from .routes.media import create_image, create_video, get_video
from .runtime import refresh_runtime, require_auth

app = FastAPI(title="AngeMedia Gateway", version="v0.1.0")
app.mount("/generated", StaticFiles(directory=str(C.OUTPUT_DIR)), name="generated")
app.mount("/uploads", StaticFiles(directory=str(C.UPLOAD_DIR)), name="uploads")
if C.FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(C.FRONTEND_DIR / "assets")), name="assets")

app.include_router(pages.router)
app.include_router(admin.router)
app.include_router(media.router)
app.include_router(storage.router)


if __name__ == "__main__":
    uvicorn.run(app, host=C.HOST, port=C.PORT)
