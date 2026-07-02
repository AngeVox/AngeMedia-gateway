"""Provider admin URL validation policy.

Saving provider configuration must be hermetic and cannot depend on DNS.
Actual outbound fetches still run the stricter SSRF/DNS policy at request time.
"""
from __future__ import annotations

import ipaddress
import urllib.parse
from typing import Any

from ..security import BLOCKED_SSRF_NETWORKS


def validate_provider_base_url(value: Any) -> str:
    url, parsed = _parse_http_url(value, label="Provider base URL")
    if parsed.query:
        raise ValueError("Provider base URL must not contain query parameters.")
    if parsed.fragment:
        raise ValueError("Provider base URL must not contain a fragment.")
    if "/images/generations" in parsed.path.rstrip("/").lower():
        raise ValueError("Provider base URL must not include /images/generations.")
    return url.rstrip("/")


def validate_provider_probe_url(value: Any) -> str:
    url, parsed = _parse_http_url(value, label="Provider status URL")
    if parsed.fragment:
        raise ValueError("Provider status URL must not contain a fragment.")
    return url


def _parse_http_url(value: Any, *, label: str) -> tuple[str, urllib.parse.ParseResult]:
    url = str(value or "").strip()
    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} is invalid.") from exc

    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{label} must start with http:// or https://.")
    if not parsed.hostname:
        raise ValueError(f"{label} is missing a hostname.")
    if parsed.username or parsed.password:
        raise ValueError(f"{label} must not contain userinfo.")
    if port is not None and not (1 <= port <= 65535):
        raise ValueError(f"{label} port is invalid.")
    _reject_obvious_private_host(parsed.hostname)
    return url, parsed


def _reject_obvious_private_host(hostname: str) -> None:
    host = hostname.strip().lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise ValueError("Provider URL must not target localhost.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or any(ip in network for network in BLOCKED_SSRF_NETWORKS)
    ):
        raise ValueError("Provider URL must not target a private or reserved IP address.")
