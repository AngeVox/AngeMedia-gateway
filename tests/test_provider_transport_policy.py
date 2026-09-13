"""Provider transport policy contracts."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.providers.http import provider_client  # noqa: E402
from angemedia_gateway.providers.transport_policy import (  # noqa: E402
    DIRECT,
    EXPLICIT_PROXY,
    resolve_provider_transport,
    validate_explicit_proxy_url,
)


class ProviderTransportPolicyTest(unittest.TestCase):
    def test_default_is_direct_and_ambient_proxy_env_is_ignored(self) -> None:
        original = {name: os.environ.get(name) for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")}
        try:
            os.environ["HTTP_PROXY"] = "http://127.0.0.1:18080"
            os.environ["HTTPS_PROXY"] = "http://127.0.0.1:18443"
            os.environ["ALL_PROXY"] = "http://127.0.0.1:19090"
            decision = resolve_provider_transport()
        finally:
            for name, value in original.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.assertEqual(decision.mode, DIRECT)
        self.assertEqual(decision.source, "default")
        self.assertIsNone(decision.proxy_url)

    def test_global_explicit_proxy_is_used_when_provider_has_no_decision(self) -> None:
        decision = resolve_provider_transport(
            {},
            {"transport_mode": EXPLICIT_PROXY, "proxy_url": "http://proxy.lan:8080"},
        )
        self.assertEqual(decision.mode, EXPLICIT_PROXY)
        self.assertEqual(decision.source, "global")
        self.assertEqual(decision.proxy_url, "http://proxy.lan:8080")

    def test_provider_explicit_proxy_overrides_global_proxy(self) -> None:
        decision = resolve_provider_transport(
            {"transport_mode": EXPLICIT_PROXY, "proxy_url": "http://provider-proxy.lan:8081"},
            {"transport_mode": EXPLICIT_PROXY, "proxy_url": "http://global-proxy.lan:8080"},
        )
        self.assertEqual(decision.source, "provider")
        self.assertEqual(decision.proxy_url, "http://provider-proxy.lan:8081")

    def test_provider_direct_bypasses_global_proxy(self) -> None:
        decision = resolve_provider_transport(
            {"transport_mode": DIRECT},
            {"transport_mode": EXPLICIT_PROXY, "proxy_url": "http://global-proxy.lan:8080"},
        )
        self.assertEqual(decision.mode, DIRECT)
        self.assertEqual(decision.source, "provider")
        self.assertIsNone(decision.proxy_url)

    def test_proxy_url_without_explicit_mode_does_not_activate_proxy(self) -> None:
        decision = resolve_provider_transport(
            {"proxy_url": "http://provider-proxy.lan:8081"},
            {"proxy_url": "http://global-proxy.lan:8080"},
        )
        self.assertEqual(decision.mode, DIRECT)
        self.assertEqual(decision.source, "default")

    def test_explicit_proxy_requires_url_and_rejects_invalid_modes(self) -> None:
        with self.assertRaises(ValueError):
            resolve_provider_transport({"transport_mode": EXPLICIT_PROXY})
        with self.assertRaises(ValueError):
            resolve_provider_transport({"transport_mode": "env"})
        with self.assertRaises(ValueError):
            resolve_provider_transport({}, {"transport_mode": "auto"})

    def test_proxy_url_validation_supports_authenticated_http_proxy(self) -> None:
        value = "http://user:secret@proxy.lan:8080"
        self.assertEqual(validate_explicit_proxy_url(value), value)

    def test_proxy_url_validation_rejects_unsupported_or_ambiguous_forms(self) -> None:
        rejected = (
            "socks5://127.0.0.1:1080",
            "ftp://proxy.example.com:21",
            "http://",
            "http://proxy.example.com:8080?route=x",
            "http://proxy.example.com:8080#fragment",
        )
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_explicit_proxy_url(value)

    def test_provider_client_default_stays_direct_and_ignores_ambient_env(self) -> None:
        with patch("angemedia_gateway.providers.http.httpx.AsyncClient") as async_client:
            provider_client()
        kwargs = async_client.call_args.kwargs
        self.assertIs(kwargs["trust_env"], False)
        self.assertNotIn("proxy", kwargs)

    def test_provider_client_uses_only_explicit_proxy_decision(self) -> None:
        decision = resolve_provider_transport(
            {"transport_mode": EXPLICIT_PROXY, "proxy_url": "http://user:secret@proxy.lan:8080"}
        )
        with patch("angemedia_gateway.providers.http.httpx.AsyncClient") as async_client:
            provider_client(transport=decision)
        kwargs = async_client.call_args.kwargs
        self.assertIs(kwargs["trust_env"], False)
        self.assertEqual(kwargs["proxy"], "http://user:secret@proxy.lan:8080")

    def test_provider_client_direct_decision_never_passes_proxy(self) -> None:
        decision = resolve_provider_transport(
            {"transport_mode": DIRECT},
            {"transport_mode": EXPLICIT_PROXY, "proxy_url": "http://global-proxy.lan:8080"},
        )
        with patch("angemedia_gateway.providers.http.httpx.AsyncClient") as async_client:
            provider_client(transport=decision)
        kwargs = async_client.call_args.kwargs
        self.assertIs(kwargs["trust_env"], False)
        self.assertNotIn("proxy", kwargs)


if __name__ == "__main__":
    unittest.main()
