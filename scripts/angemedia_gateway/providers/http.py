"""Shared provider HTTP helpers."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx

from .. import config as C
from .errors import (
    BackendUnavailable,
    ProviderAuthError,
    ProviderProtocolError,
    ProviderTimeout,
    ProviderValidationError,
    RateLimited,
)
from .transport_config import resolve_saved_provider_transport
from .transport_policy import EXPLICIT_PROXY, ProviderTransportDecision


def provider_timeout(timeout: float | None = None) -> httpx.Timeout:
    """Build the default provider timeout object."""

    value = C.HTTP_TIMEOUT if timeout is None else timeout
    return httpx.Timeout(value)


def provider_limits() -> httpx.Limits:
    """Build provider connection limits explicitly instead of relying on env defaults."""

    return httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=5.0)


def provider_client(
    *,
    timeout: float | None = None,
    provider_id: str | None = None,
    transport: ProviderTransportDecision | None = None,
) -> httpx.AsyncClient:
    """Create a Provider HTTP client with explicit transport policy only.

    Ambient proxy environment variables are always ignored. A proxy is attached
    only for an explicit transport decision. When ``transport`` is omitted and a
    ``provider_id`` is supplied, the saved Provider/global transport policy is
    resolved from SQLite.
    """

    if transport is None and provider_id:
        transport = resolve_saved_provider_transport(provider_id)

    kwargs: dict[str, Any] = {
        "timeout": provider_timeout(timeout),
        "limits": provider_limits(),
        "trust_env": False,
    }
    if transport is not None and transport.mode == EXPLICIT_PROXY:
        if not transport.proxy_url:
            raise ValueError("explicit_proxy transport is missing proxy_url")
        kwargs["proxy"] = transport.proxy_url
    return httpx.AsyncClient(**kwargs)


async def request_with_provider_errors(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    provider: str,
    operation: str,
    ok_statuses: Iterable[int] = (200,),
    **kwargs: Any,
) -> httpx.Response:
    """Perform a request and map transport/status failures to safe errors."""

    try:
        request_method = getattr(client, method.lower())
        response = await request_method(url, **kwargs)
    except Exception as exc:
        raise normalize_httpx_error(exc, provider=provider, operation=operation) from exc
    raise_for_provider_status(response, provider=provider, operation=operation, ok_statuses=ok_statuses)
    return response


def safe_json_response(response: httpx.Response, *, provider: str, operation: str) -> Any:
    """Parse JSON without leaking raw upstream body in the raised error."""

    try:
        return response.json()
    except Exception as exc:
        raise ProviderProtocolError(
            f"{provider} {operation} failed: HTTP {getattr(response, 'status_code', 'unknown')} invalid JSON",
            status_code=getattr(response, "status_code", None),
        ) from exc


def raise_for_provider_status(
    response: httpx.Response,
    *,
    provider: str,
    operation: str,
    ok_statuses: Iterable[int] = (200,),
) -> None:
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code in set(ok_statuses):
        return
    raise normalize_http_status(response, provider=provider, operation=operation)


def normalize_http_status(response: httpx.Response, *, provider: str, operation: str) -> BackendUnavailable | RateLimited:
    """Map provider HTTP status to a structured safe error."""

    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code in {401, 403}:
        return ProviderAuthError(_safe_status_message(provider, operation, status_code, "auth"), status_code=status_code)
    if status_code == 429:
        return RateLimited(_safe_status_message(provider, operation, status_code, "rate_limit"), status_code=status_code)
    if status_code in {400, 422}:
        return ProviderValidationError(_safe_status_message(provider, operation, status_code, "validation"), status_code=status_code)
    if status_code >= 500:
        return BackendUnavailable(
            _safe_status_message(provider, operation, status_code, "upstream"),
            status_code=status_code,
            retryable=True,
        )
    return BackendUnavailable(_safe_status_message(provider, operation, status_code, "upstream"), status_code=status_code)


def normalize_httpx_error(exc: Exception, *, provider: str, operation: str) -> BackendUnavailable:
    """Map transport exceptions into structured provider errors."""

    if isinstance(exc, httpx.TimeoutException):
        return ProviderTimeout(f"{provider} {operation} failed: timeout")
    return BackendUnavailable(f"{provider} {operation} failed: network", retryable=True, error_category="network")


def _safe_status_message(provider: str, operation: str, status_code: int, category: str) -> str:
    return f"{provider} {operation} failed: HTTP {status_code} {category}"
