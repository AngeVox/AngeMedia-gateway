"""Shared outbound HTTP client factories.

All service/provider traffic that may carry credentials should opt out of
ambient proxy environment variables by default.
"""
from __future__ import annotations

import httpx


def outbound_timeout(timeout: float | httpx.Timeout | None = None) -> httpx.Timeout:
    if isinstance(timeout, httpx.Timeout):
        return timeout
    return httpx.Timeout(30.0 if timeout is None else float(timeout))


def outbound_limits() -> httpx.Limits:
    return httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=5.0)


def outbound_client(
    *,
    timeout: float | httpx.Timeout | None = None,
    follow_redirects: bool = False,
) -> httpx.AsyncClient:
    """Create a credential-safe AsyncClient that ignores proxy env vars."""

    return httpx.AsyncClient(
        timeout=outbound_timeout(timeout),
        limits=outbound_limits(),
        trust_env=False,
        follow_redirects=follow_redirects,
    )
