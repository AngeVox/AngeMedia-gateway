"""Image generation orchestration."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from ..error_diagnostics import classify_provider_error
from ..helpers import now_iso, safe_json
from ..media import localize_image_result, maybe_to_b64
from ..providers.catalog.validation import CatalogOperationValidationError, validate_image_operation_request
from ..providers.custom import generate_custom_openai_image
from ..providers.errors import BackendUnavailable, RateLimited
from ..repositories.generations import record_generation
from ..repositories.settings import get_custom_provider
from ..request_hash_builders import build_image_request_hash_payload
from ..routing import resolve_chain
from ..schemas import ImageRequest
from ..security import redact_secret_text
from .generation_assets import safe_output_json, save_generated_asset
from .job_lifecycle import JobLifecycle
from .request_dedupe import IMAGE_ADMISSION_STATUSES, duplicate_response_if_in_flight, request_hash_fields

log = logging.getLogger("angemedia-gateway")


class CustomProviderNotFound(RuntimeError):
    """Requested custom provider does not exist."""


class InvalidImageRequest(RuntimeError):
    """Image request contains unsupported provider routing semantics."""


class NoImageProviderAvailable(RuntimeError):
    """No enabled image provider can handle the request."""


class ImageProvidersFailed(RuntimeError):
    """All image providers in the resolved chain failed."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("all image providers failed")
        self.errors = errors


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
    provider_id = req.model.split(":", 1)[1] if req.model else ""
    provider = get_custom_provider_func(provider_id, include_secret=True)
    if provider is None:
        raise CustomProviderNotFound(f"自定义渠道不存在：{provider_id}")
    upstream_model = _custom_upstream_model(req, provider, provider_id)

    request_hash, request_hash_version = request_hash_fields(
        build_image_request_hash_payload(
            req,
            provider_mode="custom",
            custom_provider_id=provider_id,
            custom_default_model=str(provider.get("default_model") or ""),
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
    started_at = now_iso()
    started = time.perf_counter()
    job_lifecycle.mark_running(
        job_id,
        kind="image",
        provider=f"custom:{provider_id}",
        model=upstream_model,
        started_at=started_at,
    )
    try:
        result = await generate_custom_image_func(req, provider)
        if req.response_format == "url":
            result = await localize_image_result_func(result, f"custom_{provider_id}", upstream_model, force=True)
        elif req.response_format == "b64_json":
            result = await maybe_to_b64_func(result, req.response_format)

        duration_ms = int((time.perf_counter() - started) * 1000)
        result["provider"] = f"custom:{provider_id}"
        result["model"] = upstream_model
        result["duration_ms"] = duration_ms
        return _complete_image_success(
            req=req,
            result=result,
            job_id=job_id,
            record_generation_func=record_generation_func,
            save_generated_asset_func=save_generated_asset_func,
            job_lifecycle=job_lifecycle,
            history_model=upstream_model,
            provider=f"custom:{provider_id}",
            request_model=req.model,
            input_mode="custom_provider",
            started_at=started_at,
            duration_ms=duration_ms,
            asset_model=upstream_model,
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
    chain = resolve_chain_func(req.model)
    if not chain:
        raise NoImageProviderAvailable("当前没有可用图片渠道：所选模型已停用或默认链路全部停用")
    try:
        validate_image_operation_request(req, chain)
    except CatalogOperationValidationError as exc:
        raise InvalidImageRequest(str(exc)) from exc

    request_hash, request_hash_version = request_hash_fields(
        build_image_request_hash_payload(req, provider_mode="builtin", resolved_chain=chain)
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
    errors: list[str] = []
    for target in chain:
        backend = target.provider
        model = target.model
        provider = providers.get(backend)
        if provider is None:
            errors.append(f"{backend}/{model}: unknown provider")
            continue

        started_at = now_iso()
        job_lifecycle.mark_running(job_id, kind="image", provider=backend, model=model, started_at=started_at)
        try:
            started = time.perf_counter()
            result = await provider.generate(req, target)
            if req.response_format == "url":
                result = await localize_image_result_func(result, backend, model, force=True)
            elif backend != "pollinations":
                result = await maybe_to_b64_func(result, req.response_format)

            duration_ms = int((time.perf_counter() - started) * 1000)
            result["provider"] = backend
            result["model"] = model
            result["request_model"] = req.model or ""
            result["duration_ms"] = duration_ms
            completed = _complete_image_success(
                req=req,
                result=result,
                job_id=job_id,
                record_generation_func=record_generation_func,
                save_generated_asset_func=save_generated_asset_func,
                job_lifecycle=job_lifecycle,
                history_model=model,
                provider=backend,
                request_model=req.model or "",
                input_mode="default_chain" if not req.model else "explicit_model",
                started_at=started_at,
                duration_ms=duration_ms,
                asset_model=model,
            )
            log.info("%s succeeded: model=%s", backend, model)
            return completed
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

    if job_id:
        error_msg = redact_secret_text("; ".join(errors))[:500]
        classification = classify_provider_error(error_msg)
        job_lifecycle.mark_failed(
            job_id,
            kind="image",
            error_code="all_providers_failed",
            error_message=error_msg,
            error_category=classification["error_category"],
            human_hint=classification["human_hint"],
            retryable=1 if classification["retryable"] else 0,
            gateway_stage=classification["gateway_stage"],
            completed_at=now_iso(),
        )
    raise ImageProvidersFailed(errors)


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
    error_msg = redact_secret_text(str(exc))[:500]
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
