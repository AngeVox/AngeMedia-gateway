"""网关运行时共享依赖。"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import Cookie, Header, HTTPException, UploadFile

from . import config as C
from .adapters.agnes_video import AgnesVideoProvider
from .providers.image import build_providers
from .state import (
    apply_saved_config_to_runtime,
    cleanup_admin_security_state,
    ensure_default_admin_user,
    get_admin_session,
    init_db,
)

log = logging.getLogger("angemedia-gateway")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

C.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
C.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

init_db()
ensure_default_admin_user()
cleanup_admin_security_state()
apply_saved_config_to_runtime()

PROVIDERS = build_providers()
agnes_video = AgnesVideoProvider(
    api_key=C.AGNES_API_KEY,
    base_url=C.AGNES_BASE_URL,
    timeout=C.HTTP_TIMEOUT,
    max_poll_time=C.AGNES_VIDEO_MAX_POLL_TIME,
    poll_interval=C.AGNES_VIDEO_POLL_INTERVAL,
)

UPLOAD_CHUNK_SIZE = 1024 * 1024
ALLOWED_UPLOAD_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov"}


def refresh_runtime() -> None:
    """把数据库中的运行配置重新应用到当前进程。"""
    apply_saved_config_to_runtime()
    agnes_video.api_key = C.AGNES_API_KEY
    agnes_video.base_url = C.AGNES_BASE_URL


def gateway_key_matches(authorization: Optional[str], x_api_key: Optional[str]) -> bool:
    if not C.GATEWAY_API_KEY:
        return False
    return authorization == f"Bearer {C.GATEWAY_API_KEY}" or x_api_key == C.GATEWAY_API_KEY


async def require_auth(
    am_admin_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> None:
    """校验普通 API 访问权限。"""
    if gateway_key_matches(authorization, x_api_key):
        return
    if get_admin_session(am_admin_session or "") is not None:
        return
    if not C.GATEWAY_API_KEY:
        return
    raise HTTPException(status_code=401, detail="缺少或无效的网关访问密钥")


async def require_admin_auth(
    am_admin_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict[str, Any]:
    """校验管理后台权限。"""
    if gateway_key_matches(authorization, x_api_key):
        return {"username": "gateway-key", "auth_type": "gateway_key"}
    session = get_admin_session(am_admin_session or "")
    if session is None:
        raise HTTPException(status_code=401, detail="需要登录管理后台")
    return {"username": session["username"], "auth_type": "session"}


def uploaded_file_url(filename: str) -> str:
    from urllib.parse import quote

    return f"{C.PUBLIC_BASE_URL}/uploads/{quote(filename)}"


async def write_upload_file_limited(file: UploadFile, path: Path, max_bytes: int) -> int:
    """分块保存上传文件，超过限制时立即中断并删除半成品。"""
    total = 0
    try:
        with path.open("wb") as fh:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail=f"{file.filename or 'upload'} 超过 MEDIA_DOWNLOAD_MAX_BYTES")
                fh.write(chunk)
        return total
    except Exception:
        try:
            if path.exists() and path.is_file():
                path.unlink()
        finally:
            raise


def client_ip_from_request(request: Any) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown-local"


def now_seconds() -> float:
    return time.time()
