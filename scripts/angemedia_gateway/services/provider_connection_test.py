"""内置渠道的只读接口探测；模型列表响应不能证明生成权限。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..helpers import now_iso
from ..providers.catalog.loader import load_provider_catalog
from ..providers.endpoint_policy import validate_provider_base_url
from ..providers.errors import ProviderError
from ..providers.runtime_config import MODELSCOPE_API_BASE_URL, ResolvedProviderRuntimeConfig, resolve_provider_runtime_config
from ..providers.transport_config import resolve_saved_provider_transport
from ..providers.transport_policy import ProviderTransportDecision
from ..repositories.provider_runtime_config import get_provider_runtime_config
from ..repositories.settings import builtin_provider_enabled
from .provider_test import fetch_openai_compatible_model_ids, provider_error_status, provider_test_message


MODELS_ENDPOINT_PROVIDERS = {"siliconflow", "agnes_image", "agnes_video", "openai_image"}


@dataclass(frozen=True, repr=False)
class ProviderConnectionContext:
    """固定本次探测实际使用的配置，禁止通过repr泄露密钥。"""

    runtime: ResolvedProviderRuntimeConfig
    enabled: bool
    transport: ProviderTransportDecision | None
    transport_invalid: bool
    details: dict[str, str]
    models_base_url: str | None
    project_model_ids: tuple[str, ...]


def capture_connection_context(provider_id: str) -> ProviderConnectionContext:
    runtime = resolve_provider_runtime_config(provider_id)
    stored = get_provider_runtime_config(provider_id) or {}
    shared_stored = get_provider_runtime_config("agnes_image") if provider_id == "agnes_video" else {}
    models_base_url = runtime.base_url if provider_id in MODELS_ENDPOINT_PROVIDERS else None
    project_model_ids: tuple[str, ...] = ()
    if provider_id == "modelscope" and runtime.base_url == MODELSCOPE_API_BASE_URL:
        models_base_url = f"{MODELSCOPE_API_BASE_URL}/v1"
        project_model_ids = tuple(sorted({
            model.provider_model for model in load_provider_catalog().models
            if model.provider == provider_id and model.selectable
        }))
    details = {
        "endpoint_kind": "models" if models_base_url is not None else "none",
        "base_url_source": "runtime" if runtime.base_url_override else "default",
        "api_key_source": _api_key_source(provider_id, runtime.api_key, stored, shared_stored or {}),
    }

    enabled = builtin_provider_enabled(provider_id)
    try:
        transport = resolve_saved_provider_transport(provider_id)
    except ValueError:
        return ProviderConnectionContext(runtime, enabled, None, True, details, models_base_url, project_model_ids)
    return ProviderConnectionContext(runtime, enabled, transport, False, details, models_base_url, project_model_ids)


async def probe_builtin_provider_connection(provider_id: str, *, context: ProviderConnectionContext | None = None) -> dict[str, Any]:
    context = context or capture_connection_context(provider_id)
    runtime, details = context.runtime, context.details
    if not context.enabled:
        return _result(provider_id, "disabled", "Provider is disabled.", details)
    if not runtime.api_key:
        return _result(provider_id, "not_configured", "Provider API key is not configured.", details)
    if context.models_base_url is None:
        return _result(
            provider_id,
            "unsupported",
            "No verified non-generation connection endpoint is available for this provider.",
            details,
        )

    if context.transport_invalid:
        return _result(provider_id, "failed", "Provider transport configuration is invalid.", details)

    try:
        base_url = validate_provider_base_url(context.models_base_url)
    except ValueError:
        return _result(provider_id, "failed", "Provider base URL is invalid.", details)

    started = time.perf_counter()
    try:
        models, _ = await fetch_openai_compatible_model_ids(
            base_url, runtime.api_key, provider_id=provider_id, timeout=10.0, transport=context.transport,
            require_valid_ids=provider_id == "modelscope",
        )
    except ProviderError as error:
        return _result(
            provider_id,
            "failed",
            provider_test_message(provider_error_status(error)),
            details,
            http_status=getattr(error, "status_code", None),
            duration_ms=_elapsed_ms(started),
        )
    except Exception:
        return _result(
            provider_id,
            "failed",
            "Provider connection test failed.",
            details,
            duration_ms=_elapsed_ms(started),
        )

    result = _result(
        provider_id,
        "success",
        "Model list endpoint is reachable. Generation and model access have not been verified.",
        details,
        http_status=200,
        duration_ms=_elapsed_ms(started),
        model_count=len(models),
    )
    if context.project_model_ids:
        listed = set(models)
        result["project_model_visibility"] = {
            "listed": [model for model in context.project_model_ids if model in listed],
            "not_listed": [model for model in context.project_model_ids if model not in listed],
        }
    return result


def _api_key_source(
    provider_id: str,
    effective_api_key: str,
    stored: dict[str, Any],
    shared_stored: dict[str, Any],
) -> str:
    if str(stored.get("api_key") or "").strip():
        return "runtime"
    if provider_id == "agnes_video" and str(shared_stored.get("api_key") or "").strip():
        return "runtime_shared"
    if effective_api_key:
        return "env"
    return "none"


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _result(
    provider_id: str,
    status: str,
    message: str,
    details: dict[str, str],
    *,
    http_status: int | None = None,
    duration_ms: int = 0,
    model_count: int | None = None,
) -> dict[str, Any]:
    return {
        "provider_id": provider_id,
        "status": status,
        "message": message,
        "http_status": http_status,
        "duration_ms": duration_ms,
        "checked_at": now_iso(),
        "details": details,
        "verification_scope": "models_endpoint" if details["endpoint_kind"] == "models" else "none",
        "model_list_validated": status == "success",
        "generation_verified": False,
        "model_count": model_count,
    }
