"""管理后台 API 路由。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from . import admin_auth, admin_gateway_keys
from ..config_metadata import metadata_response, validate_config_settings
from ..providers.catalog.api import catalog_api_response
from ..providers.catalog.loader import CatalogValidationError, load_provider_catalog
from ..schemas import ConfigUpdateRequest, ImageRequest, VideoRequest
from ..services.admin_service import (
    AdminService,
)
from ..services.assistant_config_service import (
    AssistantConfigService,
    AssistantConfigError,
    AssistantConnectionTestError,
    AssistantModelFetchError,
)
from ..services.dashboard_summary import DashboardSummaryService
from ..services.diagnostics_summary import DiagnosticsSummaryService
from ..services.provider_admin_service import ProviderAdminError, ProviderAdminService
from ..services.provider_runtime_config import ProviderRuntimeConfigError, ProviderRuntimeConfigService
from ..services.image_execution import CustomProviderNotFound, InvalidImageRequest, NoImageProviderAvailable
from ..services.image_job_admission import ImageJobAdmissionService
from ..services.maintenance_retention import (
    RetentionPolicyError,
    retention_cleanup,
    retention_preview,
)
from ..services.video_job_refresh import VideoJobRefreshError, VideoJobRefreshService
from ..services.video_execution import VideoProviderDisabled as QueuedVideoProviderDisabled
from ..services.video_job_admission import VideoJobAdmissionService
from ..repositories.settings import BUILTIN_PROVIDER_CONFIG_KEYS
from ..runtime import require_admin_auth

router = APIRouter()
router.include_router(admin_auth.router)
router.include_router(admin_gateway_keys.router)
admin_service = AdminService()
assistant_config_service = AssistantConfigService()
provider_admin_service = ProviderAdminService(admin_service)
provider_runtime_config_service = ProviderRuntimeConfigService()
video_job_refresh_service = VideoJobRefreshService()
image_job_admission_service = ImageJobAdmissionService()
video_job_admission_service = VideoJobAdmissionService()
dashboard_summary_service = DashboardSummaryService()
diagnostics_summary_service = DiagnosticsSummaryService(dashboard_summary_service)


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
        "stage": admitted.job.get("stage"),
        "provider": admitted.job.get("provider"),
        "model": admitted.job.get("model"),
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
        "stage": admitted.job.get("stage"),
        "provider": admitted.job.get("provider"),
        "model": admitted.job.get("model"),
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


@router.get("/v1/admin/dashboard/summary")
async def dashboard_summary(session: dict[str, Any] = Depends(require_admin_auth)) -> dict[str, Any]:
    if session.get("auth_type") != "session":
        raise HTTPException(status_code=403, detail="gateway API keys cannot access Admin Dashboard")
    return {"data": dashboard_summary_service.summary()}


@router.get("/v1/admin/diagnostics/summary")
async def diagnostics_summary(session: dict[str, Any] = Depends(require_admin_auth)) -> dict[str, Any]:
    if session.get("auth_type") != "session":
        raise HTTPException(status_code=403, detail="gateway API keys cannot access Admin Diagnostics")
    return {"data": diagnostics_summary_service.summary()}


@router.post("/v1/admin/maintenance/retention/preview")
async def maintenance_retention_preview(
    payload: dict[str, Any] = Body(default_factory=dict),
    session: dict[str, Any] = Depends(require_admin_auth),
) -> dict[str, Any]:
    if session.get("auth_type") != "session":
        raise HTTPException(status_code=403, detail="gateway API keys cannot access Admin Maintenance")
    try:
        return {"data": retention_preview(payload)}
    except RetentionPolicyError as exc:
        raise HTTPException(status_code=400, detail={"error": exc.code, "field": exc.field}) from exc


@router.post("/v1/admin/maintenance/retention/clean")
async def maintenance_retention_clean(
    payload: dict[str, Any] = Body(default_factory=dict),
    session: dict[str, Any] = Depends(require_admin_auth),
) -> dict[str, Any]:
    if session.get("auth_type") != "session":
        raise HTTPException(status_code=403, detail="gateway API keys cannot access Admin Maintenance")
    try:
        return {"ok": True, "data": retention_cleanup(payload)}
    except RetentionPolicyError as exc:
        raise HTTPException(status_code=400, detail={"error": exc.code, "field": exc.field}) from exc


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
        data = provider_admin_service.create_provider(provider)
    except ProviderAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
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
        return await assistant_config_service.list_models()
    except AssistantConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AssistantModelFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/v1/admin/assistant/models", dependencies=[Depends(require_admin_auth)])
async def list_assistant_models_from_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return await assistant_config_service.list_models(payload)
    except AssistantConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AssistantModelFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/v1/admin/assistant/test", dependencies=[Depends(require_admin_auth)])
async def test_assistant_connection(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return await assistant_config_service.test_connection(payload)
    except AssistantConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AssistantConnectionTestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
