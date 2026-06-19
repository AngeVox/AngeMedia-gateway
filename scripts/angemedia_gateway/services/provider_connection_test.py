"""Safe, non-generation connection tests for built-in providers."""
from __future__ import annotations

import time
from typing import Any

from ..helpers import now_iso
from ..providers.errors import ProviderError
from ..providers.http import provider_client, request_with_provider_errors
from ..providers.runtime_config import resolve_provider_runtime_config
from ..repositories.provider_runtime_config import get_provider_runtime_config
from ..repositories.settings import builtin_provider_enabled
from ..security import ensure_public_http_url
from .provider_test import provider_error_status, provider_test_message


SAFE_ENDPOINTS = {
    "openai_image": "models",
}


async def probe_builtin_provider_connection(provider_id: str) -> dict[str, Any]:
    runtime = resolve_provider_runtime_config(provider_id)
    stored = get_provider_runtime_config(provider_id) or {}
    endpoint_kind = SAFE_ENDPOINTS.get(provider_id, "none")
    details = {
        "endpoint_kind": endpoint_kind,
        "base_url_source": "runtime" if runtime.base_url_override else "default",
        "api_key_source": (
            "runtime"
            if str(stored.get("api_key") or "").strip()
            else ("env" if runtime.api_key else "none")
        ),
    }

    if not builtin_provider_enabled(provider_id):
        return _result(provider_id, "disabled", "Provider is disabled.", details)
    if not runtime.api_key:
        return _result(provider_id, "not_configured", "Provider API key is not configured.", details)
    if endpoint_kind == "none":
        return _result(
            provider_id,
            "unsupported",
            "No verified non-generation connection endpoint is available for this provider.",
            details,
        )

    try:
        base_url = ensure_public_http_url(runtime.base_url)
    except ValueError:
        return _result(provider_id, "failed", "Provider base URL is invalid.", details)

    started = time.perf_counter()
    try:
        async with provider_client(timeout=10.0) as client:
            response = await request_with_provider_errors(
                client,
                "GET",
                f"{base_url}/models",
                provider="built-in provider",
                operation="connection test",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {runtime.api_key}",
                },
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

    return _result(
        provider_id,
        "success",
        "Provider connection test passed.",
        details,
        http_status=int(response.status_code),
        duration_ms=_elapsed_ms(started),
    )


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
) -> dict[str, Any]:
    return {
        "provider_id": provider_id,
        "status": status,
        "message": message,
        "http_status": http_status,
        "duration_ms": duration_ms,
        "checked_at": now_iso(),
        "details": details,
    }
