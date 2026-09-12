from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import angemedia_gateway.config as C
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.providers.http import provider_client
from angemedia_gateway.providers.transport_config import resolve_saved_provider_transport
from angemedia_gateway.repositories.provider_transport_config import get_provider_transport_config
from angemedia_gateway.services.provider_transport_config import (
    ProviderTransportConfigError,
    ProviderTransportConfigService,
)


class ProviderTransportPersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="angemedia-transport-persistence-")
        self._original_db = C.DB_FILE
        C.DB_FILE = Path(self._tmp.name) / "angemedia.db"
        init_db()
        self.service = ProviderTransportConfigService()

    def tearDown(self) -> None:
        C.DB_FILE = self._original_db
        self._tmp.cleanup()

    def test_default_is_direct_and_summary_contains_no_proxy_url(self) -> None:
        summary = self.service.get_config("openai_image")
        self.assertEqual(summary["transport_mode"], None)
        self.assertEqual(summary["effective_mode"], "direct")
        self.assertEqual(summary["effective_source"], "default")
        self.assertFalse(summary["proxy_configured"])
        self.assertFalse(summary["effective_proxy_configured"])
        self.assertNotIn("proxy_url", summary)

    def test_global_proxy_is_persisted_but_never_returned(self) -> None:
        proxy = "http://alice:super-secret@127.0.0.1:7890"
        summary = self.service.update_config(
            None,
            {"transport_mode": "explicit_proxy", "proxy_url": proxy},
        )
        self.assertEqual(summary["scope"], "global")
        self.assertEqual(summary["effective_mode"], "explicit_proxy")
        self.assertTrue(summary["proxy_configured"])
        self.assertNotIn("proxy_url", summary)
        self.assertNotIn("super-secret", repr(summary))
        self.assertEqual(get_provider_transport_config(None)["proxy_url"], proxy)

    def test_provider_direct_bypasses_global_proxy(self) -> None:
        self.service.update_config(
            None,
            {"transport_mode": "explicit_proxy", "proxy_url": "http://127.0.0.1:7890"},
        )
        summary = self.service.update_config("openai_image", {"transport_mode": "direct"})
        self.assertEqual(summary["effective_mode"], "direct")
        self.assertEqual(summary["effective_source"], "provider")
        self.assertFalse(summary["effective_proxy_configured"])
        decision = resolve_saved_provider_transport("openai_image")
        self.assertEqual(decision.mode, "direct")
        self.assertIsNone(decision.proxy_url)

    def test_provider_proxy_overrides_global_proxy(self) -> None:
        self.service.update_config(
            None,
            {"transport_mode": "explicit_proxy", "proxy_url": "http://127.0.0.1:7890"},
        )
        self.service.update_config(
            "openai_image",
            {"transport_mode": "explicit_proxy", "proxy_url": "http://127.0.0.1:7891"},
        )
        decision = resolve_saved_provider_transport("openai_image")
        self.assertEqual(decision.mode, "explicit_proxy")
        self.assertEqual(decision.source, "provider")
        self.assertEqual(decision.proxy_url, "http://127.0.0.1:7891")

    def test_clearing_provider_override_restores_global_inheritance(self) -> None:
        self.service.update_config(
            None,
            {"transport_mode": "explicit_proxy", "proxy_url": "http://127.0.0.1:7890"},
        )
        self.service.update_config("openai_image", {"transport_mode": "direct"})
        summary = self.service.update_config("openai_image", {"transport_mode": None})
        self.assertIsNone(summary["transport_mode"])
        self.assertEqual(summary["effective_mode"], "explicit_proxy")
        self.assertEqual(summary["effective_source"], "global")
        self.assertTrue(summary["effective_proxy_configured"])

    def test_explicit_proxy_requires_proxy_and_invalid_proxy_is_rejected(self) -> None:
        with self.assertRaises(ProviderTransportConfigError):
            self.service.update_config("openai_image", {"transport_mode": "explicit_proxy"})
        with self.assertRaises(ProviderTransportConfigError):
            self.service.update_config(
                "openai_image",
                {"transport_mode": "explicit_proxy", "proxy_url": "socks5://127.0.0.1:1080"},
            )

    def test_proxy_can_be_cleared_when_mode_is_direct(self) -> None:
        self.service.update_config(
            "openai_image",
            {"transport_mode": "explicit_proxy", "proxy_url": "http://127.0.0.1:7890"},
        )
        summary = self.service.update_config(
            "openai_image",
            {"transport_mode": "direct", "proxy_url": None},
        )
        self.assertEqual(summary["transport_mode"], "direct")
        self.assertFalse(summary["proxy_configured"])
        self.assertEqual(summary["effective_mode"], "direct")

    def test_provider_client_consumes_persisted_proxy_and_direct_override(self) -> None:
        proxy = "http://alice:transport-secret@127.0.0.1:7890"
        self.service.update_config(
            None,
            {"transport_mode": "explicit_proxy", "proxy_url": proxy},
        )
        with patch("angemedia_gateway.providers.http.httpx.AsyncClient") as async_client:
            provider_client(provider_id="openai_image")
        kwargs = async_client.call_args.kwargs
        self.assertEqual(kwargs["proxy"], proxy)
        self.assertIs(kwargs["trust_env"], False)

        self.service.update_config("openai_image", {"transport_mode": "direct"})
        with patch("angemedia_gateway.providers.http.httpx.AsyncClient") as async_client:
            provider_client(provider_id="openai_image")
        kwargs = async_client.call_args.kwargs
        self.assertNotIn("proxy", kwargs)
        self.assertIs(kwargs["trust_env"], False)


if __name__ == "__main__":
    unittest.main()
