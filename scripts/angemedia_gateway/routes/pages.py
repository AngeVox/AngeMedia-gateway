"""前端页面路由。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import config as C

router = APIRouter()


@router.get("/")
async def studio_index() -> FileResponse:
    index_path = C.FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="前端文件不存在")
    return FileResponse(index_path, headers={"Cache-Control": "no-store"})


@router.get("/studio")
async def studio_alias() -> FileResponse:
    return await studio_index()


@router.get("/admin")
async def admin_index() -> FileResponse:
    admin_path = C.FRONTEND_DIR / "admin.html"
    if not admin_path.exists():
        raise HTTPException(status_code=404, detail="管理后台文件不存在")
    return FileResponse(admin_path, headers={"Cache-Control": "no-store"})


@router.get("/api-docs")
async def api_docs_index() -> FileResponse:
    docs_path = C.FRONTEND_DIR / "api_docs.html"
    if not docs_path.exists():
        raise HTTPException(status_code=404, detail="API 文档文件不存在")
    return FileResponse(docs_path, headers={"Cache-Control": "no-store"})
