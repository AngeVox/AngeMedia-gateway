"""文件、上传、历史和任务队列路由。"""
from __future__ import annotations

import urllib.parse
import uuid
from mimetypes import guess_type
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from .. import config as C
from ..runtime import ALLOWED_UPLOAD_SUFFIXES, require_admin_auth, require_auth, uploaded_file_url, write_upload_file_limited
from ..helpers import now_iso, safe_unlink_under
from ..svg_output import SVG_MAX_BYTES, SVG_MEDIA_TYPE, UnsafeSvgOutput, validate_static_svg
from ..db.connection import db_transaction
from ..presenters.assets import safe_asset_summary, safe_asset_summaries
from ..repositories.assets import delete_asset, get_asset, list_assets, save_asset, summarize_assets
from ..layer_paths import is_layer_relative_path
from ..media_file_response import MediaFileResponse
from ..storage_files import storage_directory, storage_parts
from ..repositories.image_layers import generated_file_visible
from ..services.pollinations_video_files import pollinations_video_visible
from ..repositories.generations import (
    clear_generations,
    clear_generations_and_collect_files,
    delete_generation_records_for_file,
    generation_metadata_by_filename,
    known_generated_local_paths,
)
from ..repositories.table_rows import list_rows
from ..repositories.uploads import delete_upload, save_upload

router = APIRouter()


class _MediaFileRoute(APIRoute):
    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        # 同路径拆成两个文档操作后，405 仍需列出两种可用方法。
        if scope["method"] not in self.methods:
            raise HTTPException(status_code=405, headers={"Allow": "GET, HEAD"})
        await super().handle(scope, receive, send)


media_file_router = APIRouter(route_class=_MediaFileRoute)

# 只声明媒体响应的 OpenAPI 形状，实际类型、Range 和鉴权仍由原处理器决定。
_FILE_CONTENT = {"application/octet-stream": {}}
_FILE_HEAD_RESPONSES = {
    206: {"description": "Partial file headers"},
    416: {"description": "Range not satisfiable"},
}
_FILE_GET_RESPONSES = {
    200: {"description": "File bytes", "content": _FILE_CONTENT},
    206: {"description": "Partial file bytes", "content": _FILE_CONTENT},
    416: _FILE_HEAD_RESPONSES[416],
}


def _media_location(base: Path, filename: str) -> tuple[Path, tuple[str, ...]]:
    try:
        parts = storage_parts(filename)
    except ValueError:
        raise HTTPException(status_code=400, detail="路径非法") from None
    return base.resolve(), parts


def _generated_location(filename: str) -> tuple[Path, tuple[str, ...]]:
    root, parts = _media_location(C.OUTPUT_DIR, filename)
    path = root.joinpath(*parts)
    if not generated_file_visible(path) or not pollinations_video_visible(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return root, parts


def _serve_svg(root: Path, parts: tuple[str, ...], *, head: bool, preview: bool = False) -> Response:
    # 校验和发送使用同一份字节。
    try:
        with storage_directory(root, parts[:-1]) as folder, folder.read(parts[-1]) as stream:
            content = stream.read(SVG_MAX_BYTES + 1)
        validate_static_svg(content)
    except (OSError, UnsafeSvgOutput):
        raise HTTPException(status_code=404, detail="SVG 作品不可用") from None
    name = urllib.parse.quote(parts[-1], safe="")
    return Response(
        content=b"" if head else content,
        media_type=f"{SVG_MEDIA_TYPE}; charset=utf-8" if preview else "application/octet-stream",
        headers={
            "Content-Length": str(len(content)),
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Content-Security-Policy": "sandbox; default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            "Content-Disposition": "inline" if preview else f"attachment; filename*=UTF-8''{name}",
        },
    )


@media_file_router.get(
    "/generated/{filename:path}", operation_id="serve_generated_file_generated__filename__get",
    dependencies=[Depends(require_auth)], response_class=Response, responses=_FILE_GET_RESPONSES,
)
@media_file_router.head(
    "/generated/{filename:path}", operation_id="serve_generated_file_generated__filename__head",
    dependencies=[Depends(require_auth)], response_class=Response, responses=_FILE_HEAD_RESPONSES,
)
async def serve_generated_file(filename: str) -> Response:
    """鉴权访问 /generated/ 下的文件。"""
    root, parts = _generated_location(filename)
    suffix = Path(parts[-1]).suffix.lower()
    if suffix == ".svgz":
        raise HTTPException(status_code=404, detail="文件不存在")
    if suffix == ".svg":
        return await run_in_threadpool(_serve_svg, root, parts, head=False)
    headers = {"Cache-Control": "private, no-store"} if is_layer_relative_path("/".join(parts)) else None
    return MediaFileResponse(root, parts, headers=headers)


@media_file_router.get(
    "/uploads/{filename:path}", operation_id="serve_upload_file_uploads__filename__get",
    dependencies=[Depends(require_auth)], response_class=Response, responses=_FILE_GET_RESPONSES,
)
@media_file_router.head(
    "/uploads/{filename:path}", operation_id="serve_upload_file_uploads__filename__head",
    dependencies=[Depends(require_auth)], response_class=Response, responses=_FILE_HEAD_RESPONSES,
)
async def serve_upload_file(filename: str) -> Response:
    """鉴权访问 /uploads/ 下的文件。"""
    root, parts = _media_location(C.UPLOAD_DIR, filename)
    if Path(parts[-1]).suffix.lower() in {".svg", ".svgz"}:
        raise HTTPException(status_code=404, detail="文件不存在")
    return MediaFileResponse(root, parts)


@media_file_router.get(
    "/v1/admin/media/{area}/{filename:path}", operation_id="serve_studio_media_v1_admin_media__area___filename__get",
    dependencies=[Depends(require_admin_auth)], response_class=Response, responses=_FILE_GET_RESPONSES,
)
@media_file_router.head(
    "/v1/admin/media/{area}/{filename:path}", operation_id="serve_studio_media_v1_admin_media__area___filename__head",
    dependencies=[Depends(require_admin_auth)], response_class=Response, responses=_FILE_HEAD_RESPONSES,
)
def serve_studio_media(area: Literal["generated", "uploads"], filename: str, request: Request, preview: bool = False) -> Response:
    """原生媒体请求复用管理会话校验和 FileResponse 的分段传输。"""
    root, parts = _generated_location(filename) if area == "generated" else _media_location(C.UPLOAD_DIR, filename)
    media_type = guess_type(parts[-1])[0] or "application/octet-stream"
    suffix = Path(parts[-1]).suffix.lower()
    if suffix in {".svg", ".svgz"}:
        if area != "generated" or suffix != ".svg":
            raise HTTPException(status_code=404, detail="文件不存在")
        return _serve_svg(root, parts, head=request.method == "HEAD", preview=preview)
    inline = media_type.startswith(("image/", "video/")) and media_type != SVG_MEDIA_TYPE
    return MediaFileResponse(
        root, parts,
        media_type=media_type if inline else "application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline" if inline else "attachment",
        },
    )


router.include_router(media_file_router)


@router.get("/v1/generated-files/orphans", dependencies=[Depends(require_admin_auth)])
async def list_orphan_generated_files(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    known = {Path(path).resolve() for path in known_generated_local_paths() if path}
    files: list[dict[str, Any]] = []
    for path in sorted(C.OUTPUT_DIR.glob("*"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        if not path.is_file():
            continue
        if path.resolve() in known:
            continue
        stat = path.stat()
        files.append({
            "filename": path.name,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "url": f"{C.PUBLIC_BASE_URL}/generated/{urllib.parse.quote(path.name)}",
        })
        if len(files) >= limit:
            break
    return {"data": files}


@router.delete("/v1/generated-files/orphans", dependencies=[Depends(require_admin_auth)])
async def delete_orphan_generated_files() -> dict[str, Any]:
    known = {Path(path).resolve() for path in known_generated_local_paths() if path}
    deleted = 0
    for path in C.OUTPUT_DIR.glob("*"):
        if not path.is_file() or path.resolve() in known:
            continue
        if safe_unlink_under(str(path), C.OUTPUT_DIR):
            deleted += 1
    return {"ok": True, "deleted_files": deleted}


@router.get("/v1/generated-files", dependencies=[Depends(require_admin_auth)])
async def list_generated_files(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    metadata = generation_metadata_by_filename()
    for path in sorted(C.OUTPUT_DIR.glob("*"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)[:limit]:
        if not path.is_file():
            continue
        stat = path.stat()
        meta = metadata.get(path.name, {})
        files.append({
            "filename": path.name,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "url": f"{C.PUBLIC_BASE_URL}/generated/{urllib.parse.quote(path.name)}",
            "media_type": meta.get("media_type") or ("video" if path.suffix.lower() in {".mp4", ".webm", ".mov"} else "image"),
            "prompt": meta.get("prompt") or "",
            "model": meta.get("model") or "",
            "provider": meta.get("provider") or "",
            "request_model": meta.get("request_model") or "",
            "duration_ms": meta.get("duration_ms") or 0,
            "status": meta.get("status") or "",
            "task_id": meta.get("task_id") or "",
            "history_created_at": meta.get("created_at") or "",
        })
    return {"data": files}


@router.delete("/v1/generated-files/{filename}", dependencies=[Depends(require_admin_auth)])
async def delete_generated_file(filename: str) -> dict[str, Any]:
    safe = Path(filename).name
    path = C.OUTPUT_DIR / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    result_url = f"{C.PUBLIC_BASE_URL}/generated/{urllib.parse.quote(safe)}"
    deleted_records = delete_generation_records_for_file(str(path), result_url)
    deleted_file = safe_unlink_under(str(path), C.OUTPUT_DIR)
    if not deleted_file:
        raise HTTPException(status_code=500, detail="数据库记录已删除，但文件删除失败，请检查文件权限")
    return {"ok": True, "deleted_records": deleted_records}


@router.delete("/v1/uploads/{upload_id}", dependencies=[Depends(require_admin_auth)])
async def remove_upload(upload_id: str) -> dict[str, Any]:
    result = delete_upload(upload_id)
    if not result.found:
        raise HTTPException(status_code=404, detail="上传文件不存在")
    if not result.file_deleted:
        raise HTTPException(status_code=500, detail="数据库记录已删除，但上传文件删除失败，请检查文件权限")
    return {"ok": True}


@router.get("/v1/history", dependencies=[Depends(require_admin_auth)])
async def get_history(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return {"data": list_rows("generations", limit)}


@router.delete("/v1/history", dependencies=[Depends(require_admin_auth)])
async def clear_history(also_delete_files: bool = Query(False)) -> dict[str, Any]:
    deleted_files = 0
    if also_delete_files:
        paths = clear_generations_and_collect_files()
        for path in paths:
            try:
                if safe_unlink_under(path, C.OUTPUT_DIR):
                    deleted_files += 1
            except HTTPException:
                continue
    else:
        clear_generations()
    return {"ok": True, "deleted_files": deleted_files}


@router.get("/v1/video-tasks", dependencies=[Depends(require_admin_auth)])
async def get_video_tasks(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return {"data": list_rows("video_tasks", limit)}


@router.get("/v1/uploads", dependencies=[Depends(require_admin_auth)])
async def get_uploads(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return {"data": list_rows("uploads", limit), "limits": {"max_files": C.UPLOAD_MAX_FILES}}


@router.post("/v1/uploads", dependencies=[Depends(require_admin_auth)])
async def upload_media(
    files: list[UploadFile] = File(...),
    roles: Optional[str] = Form(None),
) -> dict[str, Any]:
    if len(files) > C.UPLOAD_MAX_FILES:
        raise HTTPException(status_code=413, detail=f"一次最多上传 {C.UPLOAD_MAX_FILES} 个文件")
    role_list = [part.strip() for part in (roles or "").split(",") if part.strip()]
    image_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    video_suffixes = {".mp4", ".webm", ".mov"}
    saved: list[dict[str, Any]] = []
    for index, file in enumerate(files):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            suffix = ".bin"
        filename = f"upload_{uuid.uuid4().hex}{suffix}"
        path = C.UPLOAD_DIR / filename
        await write_upload_file_limited(file, path, C.MEDIA_DOWNLOAD_MAX_BYTES)
        role = role_list[index] if index < len(role_list) else "reference"
        row = {
            "id": uuid.uuid4().hex,
            "filename": filename,
            "original_filename": file.filename or "",
            "role": role,
            "content_type": file.content_type or "",
            "url": uploaded_file_url(filename),
            "local_path": str(path),
            "created_at": now_iso(),
        }
        save_upload(row)
        # 写入 assets 表（仅 image/video 类型）
        if suffix in image_suffixes:
            media_type = "image"
        elif suffix in video_suffixes:
            media_type = "video"
        else:
            media_type = None
        if media_type is not None:
            save_asset(
                id=uuid.uuid4().hex,
                filename=filename,
                storage_area="upload",
                relative_path=filename,
                url_path=f"/uploads/{filename}",
                media_type=media_type,
                source="upload",
                size=path.stat().st_size,
            )
        saved.append(row)
    return {"data": saved}


@router.get("/v1/assets", dependencies=[Depends(require_admin_auth)])
def get_assets(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    job_id: Optional[str] = Query(None),
    media_type: Optional[Literal['image', 'video']] = Query(None),
    source: Optional[Literal['generated', 'upload']] = Query(None),
) -> dict[str, Any]:
    # 同步SQLite读操作交给框架线程池；整份响应在一个快照内完成。
    with db_transaction() as conn:
        summary = summarize_assets(conn=conn, media_type=media_type, source=source, job_id=job_id)
        assets = list_assets(limit=limit, offset=offset, job_id=job_id, media_type=media_type, source=source, conn=conn)
        data = safe_asset_summaries(assets, conn=conn)
    return {'data': data, 'total': summary['total'], 'summary': summary, 'limit': limit, 'offset': offset}


@router.get("/v1/assets/{asset_id}", dependencies=[Depends(require_admin_auth)])
async def get_asset_detail(asset_id: str) -> dict[str, Any]:
    asset = get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在")
    return {"data": safe_asset_summary(asset)}


@router.delete("/v1/assets/{asset_id}", dependencies=[Depends(require_admin_auth)])
async def delete_asset_detail(asset_id: str) -> dict[str, Any]:
    deleted = delete_asset(asset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="资产不存在")
    return {"ok": True}
