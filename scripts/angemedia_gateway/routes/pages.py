"""前端页面路由。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.responses import HTMLResponse
from ..services.studio_assets import studio_assets

from .. import config as C

router = APIRouter()


@router.get("/")
async def studio_index() -> HTMLResponse:
    return studio_assets().page()


@router.get("/studio")
async def studio_alias() -> HTMLResponse:
    return await studio_index()


@router.get("/admin")
@router.get("/admin/")
async def admin_index() -> RedirectResponse:
    return RedirectResponse("/#/dashboard")


@router.get("/api-docs")
async def api_docs_index() -> FileResponse:
    docs_path = C.FRONTEND_DIR / "api_docs.html"
    if not docs_path.exists():
        raise HTTPException(status_code=404, detail="API 文档文件不存在")
    return FileResponse(docs_path, headers={"Cache-Control": "no-store"})
