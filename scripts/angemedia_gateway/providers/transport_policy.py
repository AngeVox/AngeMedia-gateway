"""Explicit Provider transport policy.

Provider traffic is direct by default and never derives proxy behavior from
ambient HTTP_PROXY/HTTPS_PROXY/ALL_PROXY variables. A proxy is used only when
an administrator explicitly selects ``explicit_proxy`` at Provider or global
scope.

This module resolves policy only. HTTP client wiring is intentionally separate.
"""
from __future__ import annotations

from dataclasses import dataclass
import urllib.parse
from typing import Any, Mapping


DIRECT = "direct"
EXPLICIT_PROXY = "explicit_proxy"
_SUPPORTED_MODES = {DIRECT, EXPLICIT_PROXY}


@dataclass(frozen=True, slots=True)
class ProviderTransportDecision:
    mode: str
    source: str
    proxy_url: str | None = None


def validate_explicit_proxy_url(value: Any) -> str:
    """Validate an administrator-configured HTTP(S) proxy URL.

    Userinfo is allowed because authenticated forward proxies commonly encode
    credentials in the proxy URL. Callers must treat the returned value as a
    secret and must never expose it in summaries or logs.
    """

    url = str(value or "").strip()
    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Provider proxy URL is invalid.") from exc

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Provider proxy URL must start with http:// or https://.")
    if not parsed.hostname:
        raise ValueError("Provider proxy URL is missing a hostname.")
    if port is not None and not (1 <= port <= 65535):
        raise ValueError("Provider proxy URL port is invalid.")
    if parsed.query or parsed.fragment:
        raise ValueError("Provider proxy URL must not contain query parameters or fragments.")
    return url.rstrip("/")


def resolve_provider_transport(
    provider_config: Mapping[str, Any] | None = None,
    global_config: Mapping[str, Any] | None = None,
) -> ProviderTransportDecision:
    """Resolve Provider transport with explicit, deterministic precedence.

    Precedence:
      1. per-Provider explicit decision (direct or explicit_proxy)
      2. global explicit decision
      3. direct default

    Merely setting a proxy URL does not activate a proxy. A mode must be
    explicitly selected, which keeps behavior auditable and avoids accidental
    ambient proxy inheritance.
    """

    provider = provider_config or {}
    global_ = global_config or {}

    provider_mode = _optional_mode(provider.get("transport_mode"), scope="Provider")
    if provider_mode is not None:
        return _decision(provider_mode, provider.get("proxy_url"), source="provider")

    global_mode = _optional_mode(global_.get("transport_mode"), scope="Global Provider")
    if global_mode is not None:
        return _decision(global_mode, global_.get("proxy_url"), source="global")

    return ProviderTransportDecision(mode=DIRECT, source="default", proxy_url=None)


def _optional_mode(value: Any, *, scope: str) -> str | None:
    mode = str(value or "").strip().lower()
    if not mode:
        return None
    if mode not in _SUPPORTED_MODES:
        raise ValueError(f"{scope} transport mode is invalid.")
    return mode


def _decision(mode: str, proxy_url: Any, *, source: str) -> ProviderTransportDecision:
    if mode == DIRECT:
        return ProviderTransportDecision(mode=DIRECT, source=source, proxy_url=None)

    value = str(proxy_url or "").strip()
    if not value:
        raise ValueError("explicit_proxy transport requires a Provider proxy URL.")
    return ProviderTransportDecision(
        mode=EXPLICIT_PROXY,
        source=source,
        proxy_url=validate_explicit_proxy_url(value),
    )
