"""文件、上传、历史和任务队列路由。"""
from __future__ import annotations

import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from starlette.responses import FileResponse

from .. import config as C
from ..runtime import ALLOWED_UPLOAD_SUFFIXES, require_admin_auth, require_auth, uploaded_file_url, write_upload_file_limited
from ..helpers import now_iso, safe_unlink_under
from ..repositories.assets import delete_asset, get_asset, list_assets, save_asset
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


def _resolve_file_under(base_dir: Path, filename: str) -> Path:
    """安全解析文件路径，防止路径穿越。"""
    resolved = (base_dir / filename).resolve()
    if not resolved.is_relative_to(base_dir.resolve()):
        raise HTTPException(status_code=400, detail="路径非法")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return resolved


@router.api_route("/generated/{filename:path}", methods=["GET", "HEAD"], dependencies=[Depends(require_auth)])
async def serve_generated_file(filename: str) -> FileResponse:
    """鉴权访问 /generated/ 下的文件。"""
    return FileResponse(_resolve_file_under(C.OUTPUT_DIR, filename))


@router.api_route("/uploads/{filename:path}", methods=["GET", "HEAD"], dependencies=[Depends(require_auth)])
async def serve_upload_file(filename: str) -> FileResponse:
    """鉴权访问 /uploads/ 下的文件。"""
    return FileResponse(_resolve_file_under(C.UPLOAD_DIR, filename))


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
    return {"data": list_rows("uploads", limit)}


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
async def get_assets(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    job_id: Optional[str] = Query(None),
) -> dict[str, Any]:
    return {"data": list_assets(limit=limit, offset=offset, job_id=job_id)}


@router.get("/v1/assets/{asset_id}", dependencies=[Depends(require_admin_auth)])
async def get_asset_detail(asset_id: str) -> dict[str, Any]:
    asset = get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在")
    return {"data": asset}


@router.delete("/v1/assets/{asset_id}", dependencies=[Depends(require_admin_auth)])
async def delete_asset_detail(asset_id: str) -> dict[str, Any]:
    deleted = delete_asset(asset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="资产不存在")
    return {"ok": True}
