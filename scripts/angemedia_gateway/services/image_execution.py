"""同步与队列流程共用的图片模型校验和执行。"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..helpers import now_iso
from ..model_routes import InvalidModelSelector
from ..job_sanitizer import sanitize_error_text
from ..media_execution_scope import current_execution_scope, require_execution_target, MediaExecutionRejected
from ..media import localize_image_result, maybe_to_b64
from ..providers.catalog.validation import CatalogOperationValidationError, validate_image_operation_request
from ..providers.catalog.loader import load_provider_catalog
from ..providers.endpoint_policy import validate_provider_base_url
from ..providers.runtime_config import resolve_live_provider_runtime_config
from ..providers.custom import generate_custom_openai_image
from ..providers.custom_capabilities import validate_custom_image_request
from ..providers.errors import BackendUnavailable, ProviderError, RateLimited
from ..repositories.settings import builtin_provider_enabled, get_custom_provider
from ..routing import MODEL_ALIASES, resolve_chain
from ..schemas import ImageRequest

log = logging.getLogger("angemedia-gateway")

TRUSTED_AGNES_IMAGE_OUTPUT_HOSTS = frozenset({"platform-outputs.agnes-ai.space"})


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


@dataclass(frozen=True)
class ImageTaskSubmission:
    """提交阶段已持久化的异步受理结果；不包含HTTP凭据。"""

    task_id: str
    model: str
    route_index: int
    dispatch_status: str = 'scheduled'


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
        try:
            validate_provider_base_url(provider.get("base_url"))
        except ValueError as exc:
            raise NoImageProviderAvailable("selected custom image provider is not configured") from exc
        try:
            validate_custom_image_request(req, provider)
        except ValueError as exc:
            raise InvalidImageRequest(str(exc)) from exc
        upstream_model = _provider_model_override(req) or str(provider.get("default_model") or "").strip()
        if not upstream_model:
            raise InvalidImageRequest("selected custom image provider requires a model")
        return ImageExecutionPlan(
            mode="custom",
            custom_provider_id=provider_id,
            custom_default_model=upstream_model,
        )

    try:
        chain = resolve_chain_func(req.model)
    except InvalidModelSelector as exc:
        raise InvalidImageRequest(str(exc)) from exc
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
        if not req.model:
            # 默认链只保留参数兼容且本地凭据齐全的候选；确认和哈希绑定实际计划。
            compatible = []
            parameter_compatible = False
            configured = {}
            catalog = load_provider_catalog()
            for target in chain:
                try:
                    validate_image_operation_request(req, [target])
                except CatalogOperationValidationError:
                    continue
                parameter_compatible = True
                entry = catalog.providers_by_id.get(str(target.provider))
                if entry is not None and entry.requires_key:
                    if entry.id not in configured:
                        configured[entry.id] = bool(resolve_live_provider_runtime_config(entry.id).api_key)
                    if not configured[entry.id]:
                        continue
                compatible.append(target)
            if not compatible:
                if parameter_compatible:
                    raise NoImageProviderAvailable('no configured image provider supports this request')
                raise CatalogOperationValidationError('no default image model supports this request')
            chain = compatible
        else:
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

    async def execute_staged(self, req: ImageRequest, plan: ImageExecutionPlan, submit) -> ImageExecutionResult | ImageTaskSubmission:
        """仅持久图片worker提供提交回调，供应商与同步入口不依赖队列。"""
        return await self._execute_builtin(req, plan, submit=submit)

    async def complete_builtin(self, req, backend, model, result, *, started_at, started=None):
        """生成响应与独立取回都复用同一份安全媒体交付。"""
        started = time.perf_counter() if started is None else started
        if req.response_format == 'url':
            kwargs = {'force': True}
            if backend == 'agnes_image': kwargs['trusted_hosts'] = TRUSTED_AGNES_IMAGE_OUTPUT_HOSTS
            result = await self.localize_image_result_func(result, backend, model, **kwargs)
        else:
            result = await self.maybe_to_b64_func(result, req.response_format)
        duration_ms = int((time.perf_counter() - started) * 1000)
        result.update(provider=backend, model=model, request_model=req.model or '', duration_ms=duration_ms)
        return ImageExecutionResult(result=result, provider=backend, model=model, request_model=req.model or '',
            input_mode='default_chain' if not req.model else 'explicit_model', duration_ms=duration_ms, started_at=started_at)

    async def _execute_custom(
        self, req: ImageRequest, plan: ImageExecutionPlan
    ) -> ImageExecutionResult:
        provider_id = str(plan.custom_provider_id or "")
        require_execution_target('custom:' + provider_id, plan.model)
        scope = current_execution_scope()
        provider = scope.custom_provider(provider_id) if scope is not None else self.get_custom_provider_func(provider_id, include_secret=True)
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
        self, req: ImageRequest, plan: ImageExecutionPlan, *, submit=None
    ) -> ImageExecutionResult | ImageTaskSubmission:
        errors: list[str] = []
        for route_index, (backend, model) in enumerate(plan.routes):
            require_execution_target(backend, model)
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
            committed = False
            try:
                if backend == 'modelscope' and submit is not None:
                    return await submit(provider, req, RouteTarget(backend, model), route_index)
                result = await provider.generate(req, RouteTarget(backend, model))
                committed = backend == 'modelscope'
                return await self.complete_builtin(req, backend, model, result, started_at=started_at, started=started)
            except MediaExecutionRejected:
                raise
            except (RateLimited, BackendUnavailable) as exc:
                if committed or exc.request_committed:
                    raise
                errors.append(f"{backend}/{model}: {sanitize_error_text(str(exc))}")
                log.warning("image provider unavailable: provider=%s error_type=%s", backend, type(exc).__name__)
            except Exception as exc:
                if committed or (backend=='modelscope' and submit is not None):
                    raise
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
