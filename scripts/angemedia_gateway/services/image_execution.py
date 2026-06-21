"""Provider-only image execution shared by synchronous and queued flows."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..helpers import now_iso
from ..job_sanitizer import sanitize_error_text
from ..media import localize_image_result, maybe_to_b64
from ..providers.catalog.validation import CatalogOperationValidationError, validate_image_operation_request
from ..providers.custom import generate_custom_openai_image
from ..providers.errors import BackendUnavailable, RateLimited
from ..repositories.settings import builtin_provider_enabled, get_custom_provider
from ..routing import MODEL_ALIASES, resolve_chain
from ..schemas import ImageRequest

log = logging.getLogger("angemedia-gateway")


class CustomProviderNotFound(RuntimeError):
    pass


class InvalidImageRequest(RuntimeError):
    pass


class NoImageProviderAvailable(RuntimeError):
    pass


class ImageProvidersFailed(RuntimeError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("all image providers failed")
        self.errors = errors


@dataclass(frozen=True)
class ImageExecutionPlan:
    mode: str
    routes: tuple[tuple[str, str], ...] = ()
    custom_provider_id: str | None = None
    custom_default_model: str | None = None

    @property
    def provider(self) -> str:
        if self.mode == "custom":
            return f"custom:{self.custom_provider_id}"
        return self.routes[0][0] if self.routes else ""

    @property
    def model(self) -> str:
        if self.mode == "custom":
            return self.custom_default_model or f"custom:{self.custom_provider_id}"
        return self.routes[0][1] if self.routes else ""

    def to_dict(self) -> dict[str, Any]:
        if self.mode == "custom":
            return {
                "mode": "custom",
                "custom_provider_id": self.custom_provider_id,
                "custom_default_model": self.custom_default_model,
            }
        return {
            "mode": "builtin",
            "routes": [
                {"provider": provider, "model": model} for provider, model in self.routes
            ],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ImageExecutionPlan":
        if not isinstance(value, Mapping):
            raise InvalidImageRequest("queued image route plan is invalid")
        mode = str(value.get("mode") or "")
        if mode == "custom":
            provider_id = str(value.get("custom_provider_id") or "").strip()
            if not provider_id:
                raise InvalidImageRequest("queued custom image route is invalid")
            return cls(
                mode="custom",
                custom_provider_id=provider_id,
                custom_default_model=str(value.get("custom_default_model") or "").strip() or None,
            )
        if mode != "builtin" or not isinstance(value.get("routes"), list):
            raise InvalidImageRequest("queued image route plan is invalid")
        routes: list[tuple[str, str]] = []
        for item in value["routes"]:
            if not isinstance(item, Mapping):
                raise InvalidImageRequest("queued image route plan is invalid")
            provider = str(item.get("provider") or "").strip()
            model = str(item.get("model") or "").strip()
            if not provider or not model:
                raise InvalidImageRequest("queued image route plan is invalid")
            routes.append((provider, model))
        if not routes:
            raise NoImageProviderAvailable("queued image route has no providers")
        return cls(mode="builtin", routes=tuple(routes))


@dataclass(frozen=True)
class ImageExecutionResult:
    result: dict[str, Any]
    provider: str
    model: str
    request_model: str | None
    input_mode: str
    duration_ms: int
    started_at: str


def _provider_model_override(req: ImageRequest) -> str | None:
    value = getattr(req, "provider_model", None)
    return str(value).strip() or None if value is not None else None


def build_image_execution_plan(
    req: ImageRequest,
    *,
    resolve_chain_func: Callable[[str | None], list[Any]] = resolve_chain,
    get_custom_provider_func: Callable[..., dict[str, Any] | None] = get_custom_provider,
    builtin_provider_enabled_func: Callable[[str], bool] = builtin_provider_enabled,
) -> ImageExecutionPlan:
    custom_route = bool(req.model and req.model.startswith("custom:"))
    if _provider_model_override(req) and not custom_route:
        raise InvalidImageRequest("provider_model is only supported with custom image providers")
    if custom_route:
        provider_id = str(req.model).split(":", 1)[1]
        provider = get_custom_provider_func(provider_id, include_secret=False)
        if provider is None:
            raise CustomProviderNotFound(f"custom image provider not found: {provider_id}")
        if not provider.get("enabled"):
            raise NoImageProviderAvailable("selected custom image provider is disabled")
        upstream_model = _provider_model_override(req) or str(
            provider.get("default_model") or f"custom:{provider_id}"
        )
        return ImageExecutionPlan(
            mode="custom",
            custom_provider_id=provider_id,
            custom_default_model=upstream_model,
        )

    chain = resolve_chain_func(req.model)
    if not chain:
        lowered = req.model.strip().lower() if req.model else ""
        if lowered and lowered in MODEL_ALIASES:
            alias = MODEL_ALIASES[lowered]
            if not builtin_provider_enabled_func(alias.provider):
                raise NoImageProviderAvailable("所选模型已停用")
        if not req.model:
            raise NoImageProviderAvailable("默认链路全部停用")
        raise NoImageProviderAvailable(
            "no enabled image provider can handle the selected model"
        )
    try:
        validate_image_operation_request(req, chain)
    except CatalogOperationValidationError as exc:
        raise InvalidImageRequest(str(exc)) from exc
    return ImageExecutionPlan(
        mode="builtin",
        routes=tuple((str(item.provider), str(item.model)) for item in chain),
    )


class ImageExecutionService:
    """Executes a previously planned image request without job or queue access."""

    def __init__(
        self,
        *,
        providers: Mapping[str, Any],
        get_custom_provider_func: Callable[..., dict[str, Any] | None] = get_custom_provider,
        generate_custom_image_func: Callable[..., Any] = generate_custom_openai_image,
        localize_image_result_func: Callable[..., Any] = localize_image_result,
        maybe_to_b64_func: Callable[..., Any] = maybe_to_b64,
        provider_enabled_func: Callable[[str], bool] = builtin_provider_enabled,
    ) -> None:
        self.providers = providers
        self.get_custom_provider_func = get_custom_provider_func
        self.generate_custom_image_func = generate_custom_image_func
        self.localize_image_result_func = localize_image_result_func
        self.maybe_to_b64_func = maybe_to_b64_func
        self.provider_enabled_func = provider_enabled_func

    async def execute(
        self, req: ImageRequest, plan: ImageExecutionPlan
    ) -> ImageExecutionResult:
        if plan.mode == "custom":
            return await self._execute_custom(req, plan)
        return await self._execute_builtin(req, plan)

    async def _execute_custom(
        self, req: ImageRequest, plan: ImageExecutionPlan
    ) -> ImageExecutionResult:
        provider_id = str(plan.custom_provider_id or "")
        provider = self.get_custom_provider_func(provider_id, include_secret=True)
        if provider is None:
            raise CustomProviderNotFound(f"custom image provider not found: {provider_id}")
        if not provider.get("enabled"):
            raise NoImageProviderAvailable("selected custom image provider is disabled")
        model = _provider_model_override(req) or str(
            provider.get("default_model") or plan.custom_default_model or f"custom:{provider_id}"
        )
        started_at = now_iso()
        started = time.perf_counter()
        result = await self.generate_custom_image_func(req, provider)
        if req.response_format == "url":
            result = await self.localize_image_result_func(
                result, f"custom_{provider_id}", model, force=True
            )
        else:
            result = await self.maybe_to_b64_func(result, req.response_format)
        duration_ms = int((time.perf_counter() - started) * 1000)
        result.update({"provider": f"custom:{provider_id}", "model": model, "duration_ms": duration_ms})
        return ImageExecutionResult(
            result=result,
            provider=f"custom:{provider_id}",
            model=model,
            request_model=req.model,
            input_mode="custom_provider",
            duration_ms=duration_ms,
            started_at=started_at,
        )

    async def _execute_builtin(
        self, req: ImageRequest, plan: ImageExecutionPlan
    ) -> ImageExecutionResult:
        errors: list[str] = []
        for backend, model in plan.routes:
            if not self.provider_enabled_func(backend):
                errors.append(f"{backend}/{model}: provider disabled")
                continue
            provider = self.providers.get(backend)
            if provider is None:
                errors.append(f"{backend}/{model}: unknown provider")
                continue
            from ..providers.base import RouteTarget

            started_at = now_iso()
            started = time.perf_counter()
            try:
                result = await provider.generate(req, RouteTarget(backend, model))
                if req.response_format == "url":
                    result = await self.localize_image_result_func(result, backend, model, force=True)
                elif backend != "pollinations":
                    result = await self.maybe_to_b64_func(result, req.response_format)
                duration_ms = int((time.perf_counter() - started) * 1000)
                result.update({
                    "provider": backend,
                    "model": model,
                    "request_model": req.model or "",
                    "duration_ms": duration_ms,
                })
                log.info("image provider succeeded: provider=%s model=%s", backend, model)
                return ImageExecutionResult(
                    result=result,
                    provider=backend,
                    model=model,
                    request_model=req.model or "",
                    input_mode="default_chain" if not req.model else "explicit_model",
                    duration_ms=duration_ms,
                    started_at=started_at,
                )
            except (RateLimited, BackendUnavailable) as exc:
                errors.append(f"{backend}/{model}: {sanitize_error_text(str(exc))}")
                log.warning("image provider unavailable: provider=%s error_type=%s", backend, type(exc).__name__)
            except Exception as exc:
                errors.append(
                    f"{backend}/{model}: unexpected {type(exc).__name__}: "
                    f"{sanitize_error_text(str(exc))}"
                )
                log.warning("image provider failed: provider=%s error_type=%s", backend, type(exc).__name__)
        raise ImageProvidersFailed(errors)


def build_runtime_image_executor() -> ImageExecutionService:
    """Build worker execution dependencies without coupling providers to queue code."""
    from ..runtime import PROVIDERS

    return ImageExecutionService(providers=PROVIDERS)
