"""媒体生成、模型列表路由。"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .. import config as C
from ..providers.base import BackendUnavailable, RateLimited
from ..routing import MODEL_ALIASES, build_route_response
from ..schemas import ImageRequest, RouteRequest, VideoRequest
from ..security import redact_secret_text
from ..error_diagnostics import classify_provider_error
from ..services.media_service import (
    CustomProviderNotFound,
    ImageProvidersFailed,
    MediaService,
    NoImageProviderAvailable,
    VideoProviderDisabled,
)
from ..state import (
    builtin_provider_enabled,
    list_custom_providers,
)
from ..runtime import require_auth

log = logging.getLogger("angemedia-gateway")
router = APIRouter()
media_service = MediaService()


async def _create_image_response(req: ImageRequest) -> dict[str, Any]:
    try:
        return await media_service.create_image(req)
    except CustomProviderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NoImageProviderAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ImageProvidersFailed as exc:
        # Classify error from collected provider errors
        error_msg = redact_secret_text("; ".join(exc.errors))[:500]
        classification = classify_provider_error(error_msg)
        raise HTTPException(
            status_code=502,
            detail={
                "message": "all image providers failed",
                "error_category": classification["error_category"],
                "human_hint": classification["human_hint"],
                "retryable": classification["retryable"],
                "gateway_stage": classification["gateway_stage"],
            }
        ) from exc
    except BackendUnavailable as exc:
        error_msg = redact_secret_text(str(exc))[:500]
        classification = classify_provider_error(error_msg)
        raise HTTPException(
            status_code=502,
            detail={
                "message": "provider unavailable",
                "error_category": classification["error_category"],
                "human_hint": classification["human_hint"],
                "retryable": classification["retryable"],
                "gateway_stage": classification["gateway_stage"],
            }
        ) from exc


async def _create_video_response(req: VideoRequest) -> dict[str, Any]:
    try:
        return await media_service.create_video(req)
    except VideoProviderDisabled as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Agnes AI 视频生成失败")
        raise HTTPException(status_code=502, detail=f"Agnes AI 视频生成失败：{exc}") from exc


async def _get_video_response(task_id: str) -> dict[str, Any]:
    try:
        return await media_service.get_video(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Agnes AI 视频任务查询失败")
        raise HTTPException(status_code=502, detail=f"Agnes AI 视频任务查询失败：{redact_secret_text(str(exc))}") from exc


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/v1/models", dependencies=[Depends(require_auth)])
async def list_models() -> dict[str, Any]:
    data = [
        {"id": name, "object": "model", "owned_by": target.provider, "enabled": True}
        for name, target in MODEL_ALIASES.items()
        if builtin_provider_enabled(target.provider)
    ]
    for provider in list_custom_providers(include_secret=False):
        if provider.get("enabled"):
            data.append({
                "id": f"custom:{provider['id']}",
                "object": "model",
                "owned_by": "custom_provider",
                "display_name": provider.get("name"),
                "default_model": provider.get("default_model"),
            })
    return {"object": "list", "data": data}


@router.post("/v1/media/route", dependencies=[Depends(require_auth)])
async def route_media(req: RouteRequest) -> dict[str, Any]:
    return build_route_response(req)


@router.post("/v1/images/generations", dependencies=[Depends(require_auth)])
async def create_image(req: ImageRequest) -> dict[str, Any]:
    return await _create_image_response(req)


@router.post("/v1/videos", dependencies=[Depends(require_auth)])
async def create_video(req: VideoRequest) -> dict[str, Any]:
    return await _create_video_response(req)


@router.get("/v1/videos/{task_id}", dependencies=[Depends(require_auth)])
async def get_video(task_id: str) -> dict[str, Any]:
    return await _get_video_response(task_id)
