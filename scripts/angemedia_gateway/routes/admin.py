"""管理后台 API 路由。"""
from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from ..config_metadata import metadata_response, validate_config_settings
from ..providers.catalog.api import catalog_api_response
from ..providers.catalog.loader import CatalogValidationError, load_provider_catalog
from ..schemas import ConfigUpdateRequest, ImageRequest, VideoRequest
from ..services.admin_service import (
    AdminService,
    AssistantConfigError,
    AssistantConnectionTestError,
    AssistantModelFetchError,
)
from ..services.provider_admin_service import ProviderAdminError, ProviderAdminService
from ..services.provider_runtime_config import ProviderRuntimeConfigError, ProviderRuntimeConfigService
from ..services.image_execution import CustomProviderNotFound, InvalidImageRequest, NoImageProviderAvailable
from ..services.image_job_admission import ImageJobAdmissionService
from ..services.video_job_refresh import VideoJobRefreshError, VideoJobRefreshService
from ..services.video_execution import VideoProviderDisabled as QueuedVideoProviderDisabled
from ..services.video_job_admission import VideoJobAdmissionService
from ..repositories.admin_auth import (
    change_admin_password,
    change_admin_username,
    clear_admin_login_failures,
    create_admin_session,
    delete_admin_session,
    get_admin_login_lock,
    get_admin_session,
    record_admin_login_failure,
    verify_admin_login,
)
from ..repositories.gateway_keys import (
    create_gateway_api_key,
    get_gateway_api_key,
    list_gateway_api_keys,
    revoke_gateway_api_key,
    update_gateway_api_key,
)
from ..repositories.settings import BUILTIN_PROVIDER_CONFIG_KEYS
from ..runtime import client_ip_from_request, now_seconds, require_admin_auth

router = APIRouter()
admin_service = AdminService()
provider_admin_service = ProviderAdminService(admin_service)
provider_runtime_config_service = ProviderRuntimeConfigService()
video_job_refresh_service = VideoJobRefreshService()
image_job_admission_service = ImageJobAdmissionService()
video_job_admission_service = VideoJobAdmissionService()


class _ProviderRuntimeConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    api_key: str | None = None
    base_url_override: str | None = None


@router.post("/v1/admin/jobs/images", status_code=status.HTTP_202_ACCEPTED)
async def submit_image_job(
    req: ImageRequest,
    session: dict[str, Any] = Depends(require_admin_auth),
) -> dict[str, Any]:
    if session.get("auth_type") != "session":
        raise HTTPException(status_code=403, detail="gateway API keys cannot submit Studio jobs")
    try:
        admitted = image_job_admission_service.submit(req)
    except InvalidImageRequest as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc), "code": "invalid_image_request"}) from exc
    except CustomProviderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NoImageProviderAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc), "code": "invalid_image_job"}) from exc
    return {
        "job_id": admitted.job["id"],
        "status": admitted.job["status"],
        "created": admitted.created,
        "dispatch_id": admitted.dispatch["id"] if admitted.dispatch else None,
    }


@router.post("/v1/admin/jobs/videos", status_code=status.HTTP_202_ACCEPTED)
async def submit_video_job(
    req: VideoRequest,
    session: dict[str, Any] = Depends(require_admin_auth),
) -> dict[str, Any]:
    if session.get("auth_type") != "session":
        raise HTTPException(status_code=403, detail="gateway API keys cannot submit Studio jobs")
    try:
        admitted = video_job_admission_service.submit(req)
    except QueuedVideoProviderDisabled as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"message": str(exc), "code": "invalid_video_job"},
        ) from exc
    return {
        "job_id": admitted.job["id"],
        "status": admitted.job["status"],
        "created": admitted.created,
        "dispatch_id": admitted.dispatch["id"] if admitted.dispatch else None,
    }


@router.post("/v1/admin/jobs/{job_id}/refresh", dependencies=[Depends(require_admin_auth)])
async def refresh_video_job(job_id: str) -> dict[str, Any]:
    """Compatibility/diagnostic refresh for legacy video jobs."""
    try:
        data = await video_job_refresh_service.refresh(job_id)
    except VideoJobRefreshError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"data": data}


@router.post("/v1/admin/login")
async def admin_login(payload: dict[str, str], response: Response, request: Request) -> dict[str, Any]:
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    client_ip = client_ip_from_request(request)
    locked_until = get_admin_login_lock(username, client_ip)
    if locked_until > 0:
        wait_seconds = max(1, int(locked_until - now_seconds()))
        raise HTTPException(status_code=429, detail=f"登录失败次数过多，请 {wait_seconds} 秒后再试")
    if not username or not password or not verify_admin_login(username, password):
        attempt = record_admin_login_failure(username, client_ip)
        if attempt.locked_until > 0:
            raise HTTPException(status_code=429, detail="登录失败次数过多，请 30 秒后再试")
        raise HTTPException(status_code=401, detail="账号或密码错误")
    clear_admin_login_failures(username, client_ip)
    token, expires_at = create_admin_session(username)
    response.set_cookie(
        "am_admin_session",
        token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("ADMIN_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"},
        max_age=7 * 24 * 3600,
        path="/",
    )
    return {"ok": True, "username": username, "expires_at": expires_at}


@router.post("/v1/admin/logout")
async def admin_logout(response: Response, am_admin_session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    if am_admin_session:
        delete_admin_session(am_admin_session)
    response.delete_cookie("am_admin_session", path="/")
    return {"ok": True}


@router.get("/v1/admin/me")
async def admin_me(session: dict[str, Any] = Depends(require_admin_auth)) -> dict[str, Any]:
    return {"authenticated": True, "username": session["username"], "auth_type": session["auth_type"]}


@router.get("/v1/admin/session")
async def admin_session_status(
    am_admin_session: Optional[str] = Cookie(None),
) -> dict[str, Any]:
    """返回登录状态，不用 401 响应打扰前端控制台。"""
    session = get_admin_session(am_admin_session or "")
    if session is None:
        return {"authenticated": False}
    return {"authenticated": True, "username": session["username"], "auth_type": "session"}


@router.get("/v1/admin/account")
async def admin_account(session: dict[str, Any] = Depends(require_admin_auth)) -> dict[str, Any]:
    if session["auth_type"] != "session":
        raise HTTPException(status_code=403, detail="网关访问密钥不能访问管理账号")
    return {"username": session["username"]}


@router.post("/v1/admin/password")
async def admin_change_password(
    payload: dict[str, str],
    response: Response,
    session: dict[str, Any] = Depends(require_admin_auth),
) -> dict[str, Any]:
    if session["auth_type"] != "session":
        raise HTTPException(status_code=400, detail="使用网关密钥鉴权时不能修改管理密码")
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    if not change_admin_password(session["username"], current_password, new_password):
        raise HTTPException(status_code=401, detail="当前密码错误")
    response.delete_cookie("am_admin_session", path="/")
    return {"ok": True}


@router.post("/v1/admin/username")
async def admin_change_username(
    payload: dict[str, str],
    response: Response,
    session: dict[str, Any] = Depends(require_admin_auth),
) -> dict[str, Any]:
    if session["auth_type"] != "session":
        raise HTTPException(status_code=403, detail="网关访问密钥不能访问管理账号")
    current_password = str(payload.get("current_password") or "")
    new_username = str(payload.get("new_username") or "")
    if not change_admin_username(session["username"], current_password, new_username):
        raise HTTPException(status_code=401, detail="当前密码错误")
    response.delete_cookie("am_admin_session", path="/")
    return {"ok": True, "username": new_username.strip()}


@router.get("/v1/admin/config", dependencies=[Depends(require_admin_auth)])
async def get_admin_config() -> dict[str, Any]:
    return admin_service.admin_config()


@router.get("/v1/admin/config-metadata", dependencies=[Depends(require_admin_auth)])
async def get_admin_config_metadata() -> dict[str, Any]:
    return metadata_response()


@router.post("/v1/admin/config", dependencies=[Depends(require_admin_auth)])
async def update_admin_config(req: ConfigUpdateRequest) -> dict[str, Any]:
    settings = validate_config_settings(dict(req.settings))
    return admin_service.save_config(settings)


@router.post("/v1/admin/gateway-key", dependencies=[Depends(require_admin_auth)])
async def create_gateway_key(save: bool = Body(True, embed=True)) -> dict[str, Any]:
    """生成 am- 前缀网关密钥。save=true 时自动写入配置并立即生效。"""
    return admin_service.create_gateway_key(save)


@router.get("/v1/admin/providers", dependencies=[Depends(require_admin_auth)])
async def get_custom_providers() -> dict[str, Any]:
    return {"data": admin_service.custom_providers()}


@router.get("/v1/admin/provider-templates", dependencies=[Depends(require_admin_auth)])
async def get_provider_templates() -> dict[str, Any]:
    return {"data": admin_service.provider_templates()}


@router.get("/v1/admin/catalog", dependencies=[Depends(require_admin_auth)])
async def get_provider_catalog() -> dict[str, Any]:
    try:
        catalog = load_provider_catalog()
    except CatalogValidationError as exc:
        raise HTTPException(status_code=500, detail="Provider catalog is invalid") from exc
    return catalog_api_response(catalog)


@router.post("/v1/admin/providers", dependencies=[Depends(require_admin_auth)])
async def save_custom_provider(provider: dict[str, Any]) -> dict[str, Any]:
    try:
        data = admin_service.save_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"data": data}


@router.get("/v1/admin/provider-configs", dependencies=[Depends(require_admin_auth)])
async def list_provider_runtime_configs() -> dict[str, Any]:
    return {"data": provider_runtime_config_service.list_configs()}


@router.post("/v1/admin/provider-configs/{provider_id}", dependencies=[Depends(require_admin_auth)])
async def update_provider_runtime_config_api(
    provider_id: str,
    payload: _ProviderRuntimeConfigUpdate,
) -> dict[str, Any]:
    try:
        data = provider_runtime_config_service.update_config(
            provider_id,
            payload.model_dump(exclude_unset=True),
        )
    except ProviderRuntimeConfigError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"data": data}


@router.post("/v1/admin/provider-configs/{provider_id}/clear-key", dependencies=[Depends(require_admin_auth)])
async def clear_provider_runtime_key(provider_id: str) -> dict[str, Any]:
    try:
        data = provider_runtime_config_service.clear_key(provider_id)
    except ProviderRuntimeConfigError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"data": data}


@router.post("/v1/admin/provider-configs/{provider_id}/test", dependencies=[Depends(require_admin_auth)])
async def test_provider_runtime_connection(provider_id: str) -> dict[str, Any]:
    try:
        data = await provider_runtime_config_service.test_connection(provider_id)
    except ProviderRuntimeConfigError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"data": data}


@router.get("/v1/admin/providers/{provider_id}", dependencies=[Depends(require_admin_auth)])
async def get_provider_detail(provider_id: str) -> dict[str, Any]:
    try:
        return {"data": provider_admin_service.provider_detail(provider_id)}
    except ProviderAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.patch("/v1/admin/providers/{provider_id}", dependencies=[Depends(require_admin_auth)])
async def edit_custom_provider(provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"data": provider_admin_service.edit_provider(provider_id, payload)}
    except ProviderAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/v1/admin/providers/{provider_id}/enabled", dependencies=[Depends(require_admin_auth)])
async def set_provider_enabled(provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    enabled = str(payload.get("enabled", "true")).strip().lower() in {"1", "true", "yes", "on"}
    return {"ok": True, "data": admin_service.set_provider_enabled(provider_id, enabled)}


@router.post("/v1/admin/providers/{provider_id}/sort", dependencies=[Depends(require_admin_auth)])
async def set_provider_sort(provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if provider_id in BUILTIN_PROVIDER_CONFIG_KEYS:
        raise HTTPException(status_code=400, detail="内置渠道排序固定；默认链路顺序由网关维护")
    try:
        sort_order = int(payload.get("sort_order"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="排序值必须是整数") from exc
    return {"ok": True, "data": admin_service.sort_provider(provider_id, sort_order)}


@router.post("/v1/admin/providers/{provider_id}/test", dependencies=[Depends(require_admin_auth)])
async def test_provider(provider_id: str) -> dict[str, Any]:
    try:
        return await provider_admin_service.test_provider(provider_id)
    except ProviderAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/v1/admin/providers/{provider_id}", dependencies=[Depends(require_admin_auth)])
async def remove_custom_provider(provider_id: str) -> dict[str, Any]:
    if not admin_service.delete_provider(provider_id):
        raise HTTPException(status_code=404, detail="自定义渠道不存在")
    return {"ok": True}


@router.get("/v1/admin/provider-status", dependencies=[Depends(require_admin_auth)])
async def get_provider_status() -> dict[str, Any]:
    """返回普通用户可读的渠道状态；自定义渠道可选查询 status/quota。"""
    try:
        return await admin_service.provider_status()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/v1/admin/assistant/models", dependencies=[Depends(require_admin_auth)])
async def list_assistant_models() -> dict[str, Any]:
    try:
        return await admin_service.list_assistant_models()
    except AssistantConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AssistantModelFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/v1/admin/assistant/test", dependencies=[Depends(require_admin_auth)])
async def test_assistant_connection(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return await admin_service.test_assistant_connection(payload)
    except AssistantConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AssistantConnectionTestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Gateway API Key 管理 ───────────────────────────────


class _GatewayKeyUpdateRequest(BaseModel):
    """PATCH /v1/admin/gateway-keys/{key_id} 请求体。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    note: str | None = None
    enabled: bool | None = None


@router.post("/v1/admin/gateway-keys", dependencies=[Depends(require_admin_auth)])
async def create_gateway_key_admin(payload: dict[str, Any]) -> dict[str, Any]:
    """创建 API 模式 API Key。完整密钥仅在本次响应中返回。"""
    name = str(payload.get("name") or "").strip()
    note = payload.get("note")
    if note is not None:
        note = str(note)
    data = create_gateway_api_key(name=name, note=note)
    return {
        "data": data,
        "warning": "完整密钥仅显示一次，请妥善保存。",
    }


@router.get("/v1/admin/gateway-keys", dependencies=[Depends(require_admin_auth)])
async def list_gateway_keys_admin() -> dict[str, Any]:
    """列出所有 API 模式 API Key。"""
    return {"data": list_gateway_api_keys()}


@router.get("/v1/admin/gateway-keys/{key_id}", dependencies=[Depends(require_admin_auth)])
async def get_gateway_key_admin(key_id: str) -> dict[str, Any]:
    """查询单个 API 模式 API Key。"""
    item = get_gateway_api_key(key_id)
    if item is None:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return {"data": item}


@router.patch("/v1/admin/gateway-keys/{key_id}", dependencies=[Depends(require_admin_auth)])
async def update_gateway_key_admin(key_id: str, req: _GatewayKeyUpdateRequest) -> dict[str, Any]:
    """更新 API 模式 API Key 的 name / note / enabled。"""
    item = update_gateway_api_key(
        key_id,
        name=req.name,
        note=req.note,
        enabled=req.enabled,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return {"data": item}


@router.delete("/v1/admin/gateway-keys/{key_id}", dependencies=[Depends(require_admin_auth)])
async def revoke_gateway_key_admin(key_id: str) -> dict[str, Any]:
    """吊销 API 模式 API Key。"""
    item = get_gateway_api_key(key_id)
    if item is None:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    ok = revoke_gateway_api_key(key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return {"ok": True}
