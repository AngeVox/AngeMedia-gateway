"""Image generation orchestration."""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from ..error_diagnostics import classify_provider_error
from ..helpers import now_iso, safe_json
from ..media import localize_image_result, maybe_to_b64
from ..providers.custom import generate_custom_openai_image
from ..repositories.generations import record_generation
from ..repositories.settings import get_custom_provider
from ..request_hash_builders import build_image_request_hash_payload
from ..routing import resolve_chain
from ..schemas import ImageRequest
from ..security import redact_secret_text
from .generation_assets import safe_output_json, save_generated_asset
from .image_execution import (
    CustomProviderNotFound,
    ImageExecutionService,
    ImageProvidersFailed,
    InvalidImageRequest,
    NoImageProviderAvailable,
    build_image_execution_plan,
)
from .job_lifecycle import JobLifecycle
from .request_dedupe import IMAGE_ADMISSION_STATUSES, duplicate_response_if_in_flight, request_hash_fields

log = logging.getLogger("angemedia-gateway")


async def create_image(
    req: ImageRequest,
    *,
    providers: Mapping[str, Any],
    resolve_chain_func: Callable[[str | None], list[Any]] = resolve_chain,
    get_custom_provider_func: Callable[..., dict[str, Any] | None] = get_custom_provider,
    generate_custom_image_func: Callable[..., Any] = generate_custom_openai_image,
    localize_image_result_func: Callable[..., Any] = localize_image_result,
    maybe_to_b64_func: Callable[..., Any] = maybe_to_b64,
    record_generation_func: Callable[..., str] = record_generation,
    save_generated_asset_func: Callable[..., None] = save_generated_asset,
    job_lifecycle: JobLifecycle | None = None,
) -> dict[str, Any]:
    lifecycle = job_lifecycle or JobLifecycle()
    custom_route = bool(req.model and req.model.startswith("custom:"))
    if _provider_model_override(req) and not custom_route:
        raise InvalidImageRequest("provider_model is only supported with custom image providers")
    if custom_route:
        return await create_custom_image(
            req,
            get_custom_provider_func=get_custom_provider_func,
            generate_custom_image_func=generate_custom_image_func,
            localize_image_result_func=localize_image_result_func,
            maybe_to_b64_func=maybe_to_b64_func,
            record_generation_func=record_generation_func,
            save_generated_asset_func=save_generated_asset_func,
            job_lifecycle=lifecycle,
        )
    return await create_builtin_image(
        req,
        providers=providers,
        resolve_chain_func=resolve_chain_func,
        localize_image_result_func=localize_image_result_func,
        maybe_to_b64_func=maybe_to_b64_func,
        record_generation_func=record_generation_func,
        save_generated_asset_func=save_generated_asset_func,
        job_lifecycle=lifecycle,
    )


async def create_custom_image(
    req: ImageRequest,
    *,
    get_custom_provider_func: Callable[..., dict[str, Any] | None],
    generate_custom_image_func: Callable[..., Any],
    localize_image_result_func: Callable[..., Any],
    maybe_to_b64_func: Callable[..., Any],
    record_generation_func: Callable[..., str],
    save_generated_asset_func: Callable[..., None],
    job_lifecycle: JobLifecycle,
) -> dict[str, Any]:
    plan = build_image_execution_plan(
        req,
        get_custom_provider_func=get_custom_provider_func,
    )
    provider_id = str(plan.custom_provider_id or "")
    upstream_model = plan.model

    request_hash, request_hash_version = request_hash_fields(
        build_image_request_hash_payload(
            req,
            provider_mode="custom",
            custom_provider_id=provider_id,
            custom_default_model=plan.custom_default_model,
        )
    )
    duplicate_response = duplicate_response_if_in_flight(
        kind="image",
        request_hash=request_hash,
        request_hash_version=request_hash_version,
        statuses=IMAGE_ADMISSION_STATUSES,
    )
    if duplicate_response is not None:
        return duplicate_response

    job_id = _create_image_job(req, request_hash, request_hash_version, job_lifecycle)
    executor = ImageExecutionService(
        providers={},
        get_custom_provider_func=get_custom_provider_func,
        generate_custom_image_func=generate_custom_image_func,
        localize_image_result_func=localize_image_result_func,
        maybe_to_b64_func=maybe_to_b64_func,
    )
    job_lifecycle.mark_running(
        job_id,
        kind="image",
        provider=plan.provider,
        model=upstream_model,
        started_at=now_iso(),
    )
    try:
        execution = await executor.execute(req, plan)
        return _complete_image_success(
            req=req,
            result=execution.result,
            job_id=job_id,
            record_generation_func=record_generation_func,
            save_generated_asset_func=save_generated_asset_func,
            job_lifecycle=job_lifecycle,
            history_model=execution.model,
            provider=execution.provider,
            request_model=execution.request_model,
            input_mode=execution.input_mode,
            started_at=execution.started_at,
            duration_ms=execution.duration_ms,
            asset_model=execution.model,
        )
    except Exception as exc:
        _mark_image_failure(job_id, exc, "custom_provider_failure", job_lifecycle)
        raise


async def create_builtin_image(
    req: ImageRequest,
    *,
    providers: Mapping[str, Any],
    resolve_chain_func: Callable[[str | None], list[Any]],
    localize_image_result_func: Callable[..., Any],
    maybe_to_b64_func: Callable[..., Any],
    record_generation_func: Callable[..., str],
    save_generated_asset_func: Callable[..., None],
    job_lifecycle: JobLifecycle,
) -> dict[str, Any]:
    plan = build_image_execution_plan(req, resolve_chain_func=resolve_chain_func)

    request_hash, request_hash_version = request_hash_fields(
        build_image_request_hash_payload(
            req,
            provider_mode="builtin",
            resolved_chain=[
                {"provider": provider, "model": model} for provider, model in plan.routes
            ],
        )
    )
    duplicate_response = duplicate_response_if_in_flight(
        kind="image",
        request_hash=request_hash,
        request_hash_version=request_hash_version,
        statuses=IMAGE_ADMISSION_STATUSES,
    )
    if duplicate_response is not None:
        return duplicate_response

    job_id = _create_image_job(req, request_hash, request_hash_version, job_lifecycle)
    executor = ImageExecutionService(
        providers=providers,
        localize_image_result_func=localize_image_result_func,
        maybe_to_b64_func=maybe_to_b64_func,
        provider_enabled_func=lambda _provider: True,
    )
    job_lifecycle.mark_running(
        job_id,
        kind="image",
        provider=plan.provider,
        model=plan.model,
        started_at=now_iso(),
    )
    try:
        execution = await executor.execute(req, plan)
        return _complete_image_success(
            req=req,
            result=execution.result,
            job_id=job_id,
            record_generation_func=record_generation_func,
            save_generated_asset_func=save_generated_asset_func,
            job_lifecycle=job_lifecycle,
            history_model=execution.model,
            provider=execution.provider,
            request_model=execution.request_model,
            input_mode=execution.input_mode,
            started_at=execution.started_at,
            duration_ms=execution.duration_ms,
            asset_model=execution.model,
        )
    except Exception as exc:
        _mark_image_failure(job_id, exc, "all_providers_failed", job_lifecycle)
        raise


def _create_image_job(
    req: ImageRequest,
    request_hash: str | None,
    request_hash_version: int | None,
    job_lifecycle: JobLifecycle,
) -> str:
    return job_lifecycle.create(
        kind="image",
        status="queued",
        prompt=req.prompt,
        input_json=safe_json({"model": req.model, "size": req.size, "response_format": req.response_format}),
        request_hash=request_hash,
        request_hash_version=request_hash_version,
    )


def _provider_model_override(req: ImageRequest) -> str | None:
    value = getattr(req, "provider_model", None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _custom_upstream_model(req: ImageRequest, provider: Mapping[str, Any], provider_id: str) -> str:
    return _provider_model_override(req) or str(provider.get("default_model") or f"custom:{provider_id}")


def _complete_image_success(
    *,
    req: ImageRequest,
    result: dict[str, Any],
    job_id: str | None,
    record_generation_func: Callable[..., str],
    save_generated_asset_func: Callable[..., None],
    job_lifecycle: JobLifecycle,
    history_model: str,
    provider: str,
    request_model: str | None,
    input_mode: str,
    started_at: str,
    duration_ms: int,
    asset_model: str,
) -> dict[str, Any]:
    record_id = record_generation_func(
        media_type="image",
        prompt=req.prompt,
        enhanced_prompt=None,
        model=history_model,
        status="completed",
        result=result,
        provider=provider,
        request_model=request_model,
        input_mode=input_mode,
        duration_ms=duration_ms,
        started_at=started_at,
        job_id=job_id,
    )
    save_generated_asset_func(
        media_type="image",
        result=result,
        prompt=req.prompt,
        model=asset_model,
        provider=provider,
        duration_ms=duration_ms,
        job_id=job_id,
    )
    result["history_id"] = record_id
    if job_id:
        job_lifecycle.mark_succeeded(
            job_id,
            kind="image",
            output_json=safe_output_json(result),
            completed_at=now_iso(),
            duration_ms=duration_ms,
        )
        result["job_id"] = job_id
    return result


def _mark_image_failure(job_id: str | None, exc: Exception, error_code: str, job_lifecycle: JobLifecycle) -> None:
    if not job_id:
        return
    detail = "; ".join(exc.errors) if isinstance(exc, ImageProvidersFailed) else str(exc)
    error_msg = redact_secret_text(detail)[:500]
    classification = classify_provider_error(error_msg)
    job_lifecycle.mark_failed(
        job_id,
        kind="image",
        error_code=error_code,
        error_message=error_msg,
        error_category=classification["error_category"],
        human_hint=classification["human_hint"],
        retryable=1 if classification["retryable"] else 0,
        gateway_stage=classification["gateway_stage"],
        completed_at=now_iso(),
    )
