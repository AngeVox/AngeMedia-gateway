"""网关运行时共享依赖。"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import Cookie, Header, HTTPException, Query, Request, UploadFile
from .security import admin_session_context
from .services.studio_assets import check_studio_revision

from . import config as C
from .adapters.agnes_video import AgnesVideoProvider
from .media import cleanup_controlled_download_tmp_dir, verify_download_tmp_os_replace_ready
from .providers.image.registry import build_providers
from .providers.runtime_config import resolve_provider_runtime_config
from .db.schema import init_db
from .repositories.admin_auth import cleanup_admin_security_state, ensure_default_admin_user, get_admin_session
from .repositories.gateway_keys import (
    has_gateway_api_key_records,
    update_gateway_api_key_last_used,
    verify_gateway_api_key,
)
from .repositories.settings import apply_saved_config_to_runtime

log = logging.getLogger("angemedia-gateway")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

C.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
C.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
cleanup_controlled_download_tmp_dir()
verify_download_tmp_os_replace_ready()

init_db()
ensure_default_admin_user()
cleanup_admin_security_state()
apply_saved_config_to_runtime()

PROVIDERS = build_providers()
agnes_video = AgnesVideoProvider(
    api_key=C.AGNES_API_KEY,
    base_url=C.AGNES_BASE_URL,
    timeout=C.VIDEO_PROVIDER_TIMEOUT,
    max_poll_time=C.AGNES_VIDEO_MAX_POLL_TIME,
    poll_interval=C.AGNES_VIDEO_POLL_INTERVAL,
    runtime_config_resolver=resolve_provider_runtime_config,
)

UPLOAD_CHUNK_SIZE = 1024 * 1024
ALLOWED_UPLOAD_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov"}


def refresh_runtime() -> None:
    """把数据库中的运行配置重新应用到当前进程。"""
    apply_saved_config_to_runtime()
    agnes_video.api_key = C.AGNES_API_KEY
    agnes_video.base_url = C.AGNES_BASE_URL
    agnes_video.timeout = C.VIDEO_PROVIDER_TIMEOUT
    agnes_video.max_poll_time = C.AGNES_VIDEO_MAX_POLL_TIME
    agnes_video.poll_interval = C.AGNES_VIDEO_POLL_INTERVAL


def _bearer_token(authorization: Optional[str]) -> str:
    value = (authorization or "").strip()
    if not value:
        return ""
    prefix = "Bearer "
    if not value.startswith(prefix):
        return ""
    return value[len(prefix):].strip()


def _request_gateway_token(authorization: Optional[str], x_api_key: Optional[str]) -> tuple[str, bool]:
    bearer = _bearer_token(authorization)
    api_key = (x_api_key or "").strip()
    if bearer and api_key and bearer != api_key:
        return "", True
    return bearer or api_key, False


def _legacy_gateway_key_matches(token: str) -> bool:
    if not token or not C.GATEWAY_API_KEY:
        return False
    return hmac.compare_digest(token, C.GATEWAY_API_KEY)


def _gateway_auth_enabled() -> bool:
    return bool(C.GATEWAY_API_KEY) or has_gateway_api_key_records()


def _valid_gateway_key_token(token: str) -> bool:
    if not token:
        return False
    if verify_gateway_api_key(token) is not None:
        return True
    return _legacy_gateway_key_matches(token)


def gateway_key_matches(authorization: Optional[str], x_api_key: Optional[str]) -> bool:
    token, conflict = _request_gateway_token(authorization, x_api_key)
    if conflict:
        return False
    return _legacy_gateway_key_matches(token)


async def require_auth(
    request: Request,
    am_admin_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_studio_session_context: Annotated[str | None, Header(alias="X-Studio-Session-Context")] = None,
    x_studio_revision: Annotated[str | None, Header(alias="X-Studio-Revision")] = None,
) -> None:
    """校验普通 API 访问权限。"""
    token, conflict = _request_gateway_token(authorization, x_api_key)
    if conflict:
        raise HTTPException(status_code=401, detail="缺少或无效的网关访问密钥")
    if token:
        record = verify_gateway_api_key(token)
        if record is not None:
            try:
                updated = update_gateway_api_key_last_used(
                    record["id"],
                    client_ip_from_request(request),
                )
                if not updated:
                    log.warning("API 模式 API Key last_used update skipped: key_id=%s", record["id"])
            except Exception:
                log.warning("API 模式 API Key last_used update failed: key_id=%s", record["id"])
            return
    if _legacy_gateway_key_matches(token):
        return
    if get_admin_session(am_admin_session or "") is not None:
        check_admin_session_context(am_admin_session or "", x_studio_session_context)
        check_studio_revision(x_studio_revision)
        return
    raise HTTPException(status_code=401, detail="缺少或无效的网关访问密钥")


def check_admin_session_context(token: str, expected: str | None) -> None:
    """Cookie客户端必须绑定原会话；引导读取与API Key不经过此检查。"""
    if expected is None:
        raise HTTPException(status_code=409, detail={"code": "session_context_required"})
    if expected is not None and (len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected)
                                 or not hmac.compare_digest(admin_session_context(token), expected)):
        raise HTTPException(status_code=409, detail={"code": "session_context_changed", "message": "Session context changed"})


async def require_admin_auth(
    am_admin_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_studio_session_context: Annotated[str | None, Header(alias="X-Studio-Session-Context")] = None,
    studio_session_context: Annotated[str | None, Query()] = None,
    x_studio_revision: Annotated[str | None, Header(alias="X-Studio-Revision")] = None,
    studio_revision: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """校验管理后台权限。"""
    if x_studio_session_context is not None and studio_session_context is not None and x_studio_session_context != studio_session_context:
        raise HTTPException(status_code=409, detail={"code": "session_context_changed", "message": "Session context changed"})
    expected = x_studio_session_context if x_studio_session_context is not None else studio_session_context
    revision = x_studio_revision if x_studio_revision is not None else studio_revision
    if x_studio_revision is not None and studio_revision is not None and x_studio_revision != studio_revision:
        raise HTTPException(status_code=409, detail={"code": "studio_upgrade_required"})
    return await asyncio.to_thread(_authenticate_admin, am_admin_session, authorization, x_api_key, expected, revision)


def _authenticate_admin(am_admin_session: Optional[str], authorization: Optional[str],
                        x_api_key: Optional[str], expected: str | None = None, revision: str | None = None) -> dict[str, Any]:
    """会话与网关密钥的SQLite校验在线程内完成，避免阻塞事件循环。"""
    session = get_admin_session(am_admin_session or "")
    if session is not None:
        check_admin_session_context(am_admin_session or "", expected)
        check_studio_revision(revision)
        return {"username": session["username"], "auth_type": "session"}
    token, conflict = _request_gateway_token(authorization, x_api_key)
    if conflict or _valid_gateway_key_token(token):
        raise HTTPException(status_code=403, detail="网关访问密钥不能访问管理后台")
    raise HTTPException(status_code=401, detail="需要登录管理后台")


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
