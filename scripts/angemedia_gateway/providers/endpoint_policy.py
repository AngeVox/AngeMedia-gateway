"""Provider admin URL validation policy.

Configured provider endpoints are explicit administrator-controlled capabilities.
They may legitimately target localhost, RFC1918/ULA networks, overlay networks,
or public relay services. This policy therefore validates URL structure without
reusing the stricter SSRF rules that protect user-supplied media URLs.

User-controlled downloads and reference URLs must continue to use the strict
security helpers in ``security.py``.
"""
from __future__ import annotations

import ipaddress
import urllib.parse
from typing import Any


_CONFIGURED_LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "::1/128",
        "fc00::/7",
    )
)

_BLOCKED_METADATA_HOSTS = {
    "metadata.google.internal",
    "metadata.azure.internal",
    "instance-data.ec2.internal",
}

_BLOCKED_METADATA_IPS = {
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("169.254.169.254"),
}


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
    _reject_disallowed_configured_host(parsed.hostname)
    return url, parsed


def _reject_disallowed_configured_host(hostname: str) -> None:
    """Reject only targets that should never be a configured Provider endpoint.

    Localhost, RFC1918, ULA, and CGNAT are intentionally accepted because a
    self-hosted gateway may point at a local New-API/one-api instance or an
    overlay-network relay. DNS is intentionally not resolved while saving the
    configuration so split-horizon/Fake-IP setups stay deterministic.
    """

    host = hostname.strip().lower().rstrip(".")
    if host in _BLOCKED_METADATA_HOSTS:
        raise ValueError("Provider URL must not target a metadata service.")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Hostnames (including .lan/.internal and public relay domains) are
        # administrator-controlled configuration and are not resolved here.
        return

    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped

    if ip in _BLOCKED_METADATA_IPS:
        raise ValueError("Provider URL must not target a metadata service.")
    if any(ip in network for network in _CONFIGURED_LOCAL_NETWORKS):
        return
    if ip.is_link_local:
        raise ValueError("Provider URL must not target a link-local or metadata address.")
    if ip.is_multicast:
        raise ValueError("Provider URL must not target a multicast address.")
    if ip.is_unspecified:
        raise ValueError("Provider URL must not target an unspecified address.")
    if ip.is_private or ip.is_reserved:
        raise ValueError("Provider URL targets an unsupported special-use address.")
