"""Bounded network diagnostics for configured built-in Providers.

The assistant accepts only a provider id. Endpoint authorization and outbound
transport are owned by the Provider layer. Direct-mode diagnostics may resolve
and open a credential-free TCP/TLS socket; explicit-proxy Providers skip direct
target probing because that path would not represent real Provider traffic.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
import time
from typing import Any
from urllib.parse import urlparse

from ..providers.endpoint_policy import validate_provider_base_url
from ..providers.runtime_config import resolve_provider_runtime_config
from ..providers.transport_config import resolve_saved_provider_transport
from ..providers.transport_policy import EXPLICIT_PROXY

_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_METADATA_IPS = {
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("169.254.169.254"),
}


def _address_class(ip: ipaddress._BaseAddress) -> str:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if ip in _METADATA_IPS:
        return "metadata"
    if ip in _FAKE_IP_NETWORK:
        return "fake_ip"
    if ip.is_link_local:
        return "link_local"
    if ip.is_loopback or ip.is_private:
        return "local"
    if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return "special"
    return "public"


async def probe_builtin_provider_network(provider_id: str, *, timeout: float = 4.0) -> dict[str, Any]:
    """Probe the effective built-in Provider network path without credentials."""

    runtime = resolve_provider_runtime_config(provider_id)
    if not runtime.base_url:
        return {
            "provider_id": provider_id,
            "status": "not_configured",
            "message": "Provider base URL is not configured.",
        }

    try:
        base_url = validate_provider_base_url(runtime.base_url)
        parsed = urlparse(base_url)
        host = str(parsed.hostname or "").lower().rstrip(".")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        transport = resolve_saved_provider_transport(provider_id)
    except ValueError:
        return {
            "provider_id": provider_id,
            "status": "blocked",
            "message": "Provider endpoint or transport configuration is invalid.",
        }

    base = {
        "provider_id": provider_id,
        "base_url_source": "override" if runtime.base_url_override else "default",
        "transport_mode": transport.mode,
        "transport_source": transport.source,
    }
    if transport.mode == EXPLICIT_PROXY:
        return {
            **base,
            "status": "proxy_delegated",
            "message": "Provider uses an explicit proxy; direct target DNS/TCP probing was skipped.",
        }

    started = time.perf_counter()
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {**base, "status": "dns_timeout", "message": "Provider DNS lookup timed out.",
                "duration_ms": int((time.perf_counter() - started) * 1000)}
    except socket.gaierror:
        return {**base, "status": "dns_failed", "message": "Provider hostname could not be resolved.",
                "duration_ms": int((time.perf_counter() - started) * 1000)}

    addresses: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        ip_text = str(sockaddr[0])
        if ip_text in seen:
            continue
        seen.add(ip_text)
        cls = _address_class(ipaddress.ip_address(ip_text))
        addresses.append((ip_text, cls))

    if not addresses:
        return {**base, "status": "dns_failed", "message": "Provider hostname returned no usable addresses.",
                "duration_ms": int((time.perf_counter() - started) * 1000)}

    classes = {cls for _ip, cls in addresses}
    if classes & {"metadata", "link_local", "special"}:
        return {
            **base,
            "status": "blocked",
            "message": "Provider DNS resolved to a metadata or unsupported special-use address; socket probe was skipped.",
            "dns_class": "mixed_or_blocked" if len(classes) > 1 else next(iter(classes)),
            "address_count": len(addresses),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

    priority = {"public": 0, "local": 1, "fake_ip": 2}
    ip_text, cls = sorted(addresses, key=lambda item: priority.get(item[1], 9))[0]
    connect_started = time.perf_counter()
    ssl_context = ssl.create_default_context() if parsed.scheme == "https" else None
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host=ip_text,
                port=port,
                ssl=ssl_context,
                server_hostname=host if ssl_context is not None else None,
            ),
            timeout=timeout,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    except asyncio.TimeoutError:
        return {**base, "status": "connect_timeout", "message": "Provider TCP/TLS connection timed out.",
                "dns_class": cls, "address_count": len(addresses),
                "tcp_tls_ms": int((time.perf_counter() - connect_started) * 1000)}
    except (OSError, ssl.SSLError):
        return {**base, "status": "connect_failed", "message": "Provider TCP/TLS connection failed.",
                "dns_class": cls, "address_count": len(addresses),
                "tcp_tls_ms": int((time.perf_counter() - connect_started) * 1000)}

    return {
        **base,
        "status": "success",
        "message": "Provider DNS and TCP/TLS probe passed.",
        "scheme": parsed.scheme,
        "port": port,
        "dns_class": cls,
        "address_count": len(addresses),
        "tcp_tls_ms": int((time.perf_counter() - connect_started) * 1000),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
