"""渠道模型列表解析和安全诊断的共享边界。"""
from __future__ import annotations

import time
from typing import Any

from ..providers.errors import (
    BackendUnavailable,
    ProviderAuthError,
    ProviderError,
    ProviderProtocolError,
    ProviderTimeout,
    RateLimited,
)
from ..providers.http import provider_client, request_with_provider_errors, safe_json_response
from ..providers.parsers import require_mapping
from ..providers.transport_policy import ProviderTransportDecision


def provider_error_status(error: ProviderError) -> str:
    if isinstance(error, ProviderAuthError):
        return "auth_failed"
    if isinstance(error, RateLimited):
        return "rate_limited"
    if isinstance(error, ProviderTimeout):
        return "timeout"
    if isinstance(error, ProviderProtocolError):
        return "invalid_response"
    if isinstance(error, BackendUnavailable) and error.error_category == "network":
        return "network_error"
    return "upstream_unavailable"


def provider_test_message(status: str) -> str:
    return {
        "ok": "Provider test passed",
        "model_not_found": "Provider is reachable, but the default model was not listed.",
        "auth_failed": "Provider authentication failed.",
        "rate_limited": "Provider rate limit was reached.",
        "timeout": "Provider test timed out.",
        "network_error": "Provider network request failed.",
        "upstream_unavailable": "Provider upstream is unavailable.",
        "invalid_response": "Provider returned an invalid /models response.",
        "invalid_base_url": "Provider base URL is invalid.",
        "test_not_supported": "Provider test is only supported for custom OpenAI-compatible image providers.",
    }.get(status, "Provider test failed.")


async def fetch_openai_compatible_model_ids(
    base_url: str,
    api_key: str,
    *,
    provider_id: str | None = None,
    timeout: float = 10.0,
    transport: ProviderTransportDecision | None = None,
    require_valid_ids: bool = False,
) -> tuple[list[str], int]:
    """只读校验兼容接口的模型列表，不返回上游原始错误正文。"""

    started = time.perf_counter()
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with provider_client(timeout=timeout, provider_id=provider_id, transport=transport) as client:
        response = await request_with_provider_errors(
            client,
            "GET",
            f"{base_url.rstrip('/')}/models",
            provider="custom image provider",
            operation="models",
            headers=headers,
        )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    data = require_mapping(
        safe_json_response(response, provider="custom image provider", operation="models"),
        provider="custom image provider",
        operation="models",
    )
    return _model_ids_from_response(data, require_valid_ids=require_valid_ids), elapsed_ms


def _model_ids_from_response(data: dict[str, Any], *, require_valid_ids: bool = False) -> list[str]:
    items = data.get("data")
    if not isinstance(items, list):
        raise ProviderProtocolError("custom image provider models returned an invalid data field")

    ids: list[str] = []
    for item in items:
        model_id = item.get("id") if isinstance(item, dict) else None
        if isinstance(model_id, str) and model_id:
            if not require_valid_ids or (model_id.strip() == model_id and model_id.isprintable()):
                ids.append(model_id)
                continue
        if require_valid_ids:
            raise ProviderProtocolError("Provider model list contains an invalid model ID")
    return sorted(set(ids))
