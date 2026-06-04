"""媒体生成、模型列表和小助手路由。"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .. import config as C
from ..assistant import assistant_enabled, build_assistant_plan
from ..media import localize_image_result, localize_video_result, maybe_to_b64
from ..providers.base import BackendUnavailable, RateLimited
from ..providers.custom import generate_custom_openai_image
from ..routing import MODEL_ALIASES, build_route_response, enhance_prompt_text, resolve_chain
from ..schemas import AssistantRequest, EnhanceRequest, ImageRequest, RouteRequest, VideoRequest
from ..security import redact_secret_text, validate_task_id
from ..state import (
    builtin_provider_enabled,
    get_config,
    get_custom_provider,
    list_custom_providers,
    now_iso,
    record_generation,
    upsert_video_task,
)
from ..runtime import PROVIDERS, agnes_video, require_auth

log = logging.getLogger("angemedia-gateway")
router = APIRouter()


@router.get("/health")
async def health() -> dict[str, Any]:
    enabled_models = [name for name, target in MODEL_ALIASES.items() if builtin_provider_enabled(target.provider)]
    return {
        "name": "AngeMedia Gateway",
        "version": "v0.1.0",
        "status": "ok",
        "auth_enabled": bool(C.GATEWAY_API_KEY),
        "siliconflow": {
            "enabled": builtin_provider_enabled("siliconflow"),
            "configured": bool(C.SILICONFLOW_API_KEY),
        },
        "modelscope": {
            "enabled": builtin_provider_enabled("modelscope"),
            "configured": bool(C.MODELSCOPE_API_KEY),
        },
        "pollinations": {
            "enabled": builtin_provider_enabled("pollinations"),
            "configured": True,
        },
        "openai_image": {
            "enabled": builtin_provider_enabled("openai_image"),
            "configured": bool(C.OPENAI_IMAGE_API_KEY),
        },
        "agnes_image": {
            "enabled": builtin_provider_enabled("agnes_image"),
            "configured": bool(C.AGNES_API_KEY),
        },
        "agnes_video": {
            "enabled": builtin_provider_enabled("agnes_video"),
            "configured": bool(C.AGNES_API_KEY),
        },
        "storage_ready": C.OUTPUT_DIR.exists() and C.UPLOAD_DIR.exists() and C.DB_FILE.parent.exists(),
        "assistant": {
            "enabled": assistant_enabled(),
            "configured": bool(get_config("ANGE_LLM_API_KEY", os.getenv("ANGE_LLM_API_KEY", "")).strip()),
        },
        "models": enabled_models,
    }


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


@router.post("/v1/prompt/enhance", dependencies=[Depends(require_auth)])
async def enhance_prompt(req: EnhanceRequest) -> dict[str, Any]:
    media_type = req.media_type if req.media_type != "auto" else build_route_response(RouteRequest(prompt=req.prompt))["media_type"]
    enhanced, changed, notes = enhance_prompt_text(req)
    return {
        "media_type": media_type,
        "original_prompt": req.prompt,
        "enhanced_prompt": enhanced,
        "changed": changed,
        "notes": notes,
    }


@router.post("/v1/assistant/plan", dependencies=[Depends(require_auth)])
async def assistant_plan(req: AssistantRequest) -> dict[str, Any]:
    try:
        return await build_assistant_plan(req)
    except BackendUnavailable as exc:
        raise HTTPException(status_code=502, detail=redact_secret_text(str(exc))) from exc


@router.post("/v1/assistant/generate", dependencies=[Depends(require_auth)])
async def assistant_generate(req: AssistantRequest) -> dict[str, Any]:
    try:
        plan = await build_assistant_plan(req)
    except BackendUnavailable as exc:
        raise HTTPException(status_code=502, detail=redact_secret_text(str(exc))) from exc
    if req.confirm_plan or get_config("ANGE_ASSISTANT_CONFIRM_PLAN", "false").lower() in {"1", "true", "yes", "on"}:
        return {"requires_confirmation": True, "plan": plan}

    if plan["media_type"] == "video":
        video_req = VideoRequest(
            prompt=plan["prompt"],
            model="agnes-video-v2.0",
            image=plan.get("image"),
            images=plan.get("images"),
            mode=plan.get("mode"),
            width=int(plan.get("width", 1152)),
            height=int(plan.get("height", 768)),
            num_frames=int(plan.get("num_frames", 121)),
            frame_rate=float(plan.get("frame_rate", 24)),
            wait_for_completion=bool(plan.get("wait_for_completion", req.wait_for_completion)),
        )
        result = await create_video(video_req)
        result["assistant_plan"] = plan
        return result

    image_req = ImageRequest(
        prompt=plan["prompt"],
        model=plan.get("model"),
        size=plan.get("size", "1024x1024"),
        response_format="url",
        negative_prompt=plan.get("negative_prompt"),
    )
    result = await create_image(image_req)
    result["assistant_plan"] = plan
    return result


@router.post("/v1/images/generations", dependencies=[Depends(require_auth)])
async def create_image(req: ImageRequest) -> dict[str, Any]:
    if req.model and req.model.startswith("custom:"):
        provider_id = req.model.split(":", 1)[1]
        provider = get_custom_provider(provider_id, include_secret=True)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"自定义渠道不存在：{provider_id}")
        try:
            started_at = now_iso()
            started = time.perf_counter()
            result = await generate_custom_openai_image(req, provider)
            if req.response_format == "url":
                result = await localize_image_result(result, f"custom_{provider_id}", provider.get("default_model", "custom"))
            elif req.response_format == "b64_json":
                result = await maybe_to_b64(result, req.response_format)
            duration_ms = int((time.perf_counter() - started) * 1000)
            result["provider"] = f"custom:{provider_id}"
            result["model"] = str(provider.get("default_model") or f"custom:{provider_id}")
            result["duration_ms"] = duration_ms
            record_id = record_generation(
                media_type="image",
                prompt=req.prompt,
                enhanced_prompt=None,
                model=f"custom:{provider_id}",
                status="completed",
                result=result,
                provider=f"custom:{provider_id}",
                request_model=req.model,
                input_mode="custom_provider",
                duration_ms=duration_ms,
                started_at=started_at,
            )
            result["history_id"] = record_id
            return result
        except RateLimited as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except BackendUnavailable as exc:
            raise HTTPException(status_code=502, detail=redact_secret_text(str(exc))) from exc

    chain = resolve_chain(req.model)
    if not chain:
        raise HTTPException(status_code=503, detail="当前没有可用图片渠道：所选模型已停用或默认链路全部停用")
    errors: list[str] = []

    for target in chain:
        backend = target.provider
        model = target.model
        provider = PROVIDERS.get(backend)
        if provider is None:
            errors.append(f"{backend}/{model}: unknown provider")
            continue

        try:
            started_at = now_iso()
            started = time.perf_counter()
            result = await provider.generate(req, target)
            if req.response_format == "url":
                result = await localize_image_result(result, backend, model)
            elif backend != "pollinations":
                result = await maybe_to_b64(result, req.response_format)

            duration_ms = int((time.perf_counter() - started) * 1000)
            result["provider"] = backend
            result["model"] = model
            result["request_model"] = req.model or ""
            result["duration_ms"] = duration_ms
            record_id = record_generation(
                media_type="image",
                prompt=req.prompt,
                enhanced_prompt=None,
                model=model,
                status="completed",
                result=result,
                provider=backend,
                request_model=req.model or "",
                input_mode="default_chain" if not req.model else "explicit_model",
                duration_ms=duration_ms,
                started_at=started_at,
            )
            result["history_id"] = record_id
            log.info("%s succeeded: model=%s", backend, model)
            return result
        except RateLimited as exc:
            message = f"{backend}/{model}: {exc}"
            log.warning(message)
            errors.append(message)
            continue
        except BackendUnavailable as exc:
            message = f"{backend}/{model}: {exc}"
            log.warning(message)
            errors.append(message)
            continue
        except Exception as exc:
            message = f"{backend}/{model}: unexpected {type(exc).__name__}: {exc}"
            log.exception(message)
            errors.append(message)
            continue

    raise HTTPException(status_code=502, detail={"message": "all image providers failed", "errors": errors})


@router.post("/v1/videos", dependencies=[Depends(require_auth)])
async def create_video(req: VideoRequest) -> dict[str, Any]:
    if not builtin_provider_enabled("agnes_video"):
        raise HTTPException(status_code=503, detail="Agnes 视频渠道已停用，请在管理后台恢复后再生成")
    try:
        started_at = now_iso()
        started = time.perf_counter()
        if req.wait_for_completion:
            result = await agnes_video.generate_video(req)
            result = await localize_video_result(result)
            duration_ms = int((time.perf_counter() - started) * 1000)
            result["provider"] = "agnes_video"
            result["model"] = req.model
            result["duration_ms"] = duration_ms
            task_id = str(result.get("task_id") or result.get("id") or "")
            if task_id:
                upsert_video_task(task_id, req.prompt, req.model, str(result.get("status") or "completed"), result, duration_ms=duration_ms)
            record_id = record_generation(
                media_type="video",
                prompt=req.prompt,
                enhanced_prompt=None,
                model=req.model,
                status=str(result.get("status") or "completed"),
                result=result,
                task_id=task_id or None,
                provider="agnes_video",
                request_model=req.model,
                input_mode=req.mode or ("image" if req.image or req.images else "text"),
                duration_ms=duration_ms,
                started_at=started_at,
            )
            result["history_id"] = record_id
            return result

        result = await agnes_video.submit_task(req)
        duration_ms = int((time.perf_counter() - started) * 1000)
        result["provider"] = "agnes_video"
        result["model"] = req.model
        result["duration_ms"] = duration_ms
        task_id = str(result.get("task_id") or result.get("id") or "")
        if task_id:
            upsert_video_task(task_id, req.prompt, req.model, str(result.get("status") or "submitted"), result, duration_ms=duration_ms)
        record_id = record_generation(
            media_type="video",
            prompt=req.prompt,
            enhanced_prompt=None,
            model=req.model,
            status=str(result.get("status") or "submitted"),
            result=result,
            task_id=task_id or None,
            provider="agnes_video",
            request_model=req.model,
            input_mode=req.mode or ("image" if req.image or req.images else "text"),
            duration_ms=duration_ms,
            started_at=started_at,
        )
        result["history_id"] = record_id
        return result
    except Exception as exc:
        log.exception("Agnes AI 视频生成失败")
        raise HTTPException(status_code=502, detail=f"Agnes AI 视频生成失败：{exc}") from exc


@router.get("/v1/videos/{task_id}", dependencies=[Depends(require_auth)])
async def get_video(task_id: str) -> dict[str, Any]:
    try:
        task_id = validate_task_id(task_id)
        result = await agnes_video.poll_task(task_id)
        result = await localize_video_result(result)
        upsert_video_task(
            task_id,
            str(result.get("prompt") or ""),
            str(result.get("model") or "agnes-video-v2.0"),
            str(result.get("status") or "unknown"),
            result,
            duration_ms=int(result.get("duration_ms") or 0),
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Agnes AI 视频任务查询失败")
        raise HTTPException(status_code=502, detail=f"Agnes AI 视频任务查询失败：{redact_secret_text(str(exc))}") from exc
