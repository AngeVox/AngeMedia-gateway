from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import angemedia_gateway.config as C
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.reference_relay_config import get_reference_relay_config
from angemedia_gateway.services.reference_relay_config import (
    DISABLED,
    EXTERNAL_HTTP,
    ReferenceRelayConfigError,
    ReferenceRelayConfigService,
)


class ReferenceRelayConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="angemedia-reference-relay-config-")
        self._original_db = C.DB_FILE
        C.DB_FILE = Path(self._tmp.name) / "angemedia.db"
        init_db()
        self.service = ReferenceRelayConfigService()

    def tearDown(self) -> None:
        C.DB_FILE = self._original_db
        self._tmp.cleanup()

    def test_default_is_disabled_and_safe(self) -> None:
        summary = self.service.get_config()
        self.assertEqual(summary["mode"], DISABLED)
        self.assertFalse(summary["configured"])
        self.assertFalse(summary["upload_url_configured"])
        self.assertFalse(summary["token_configured"])
        self.assertNotIn("upload_url", summary)
        self.assertNotIn("token", summary)

    def test_external_http_requires_upload_url(self) -> None:
        with self.assertRaises(ReferenceRelayConfigError):
            self.service.update_config({"mode": EXTERNAL_HTTP})

    def test_private_admin_upload_endpoint_is_allowed_but_not_echoed(self) -> None:
        token = "relay-secret-token"
        url = "http://192.168.1.10:8787/upload"
        summary = self.service.update_config({
            "mode": EXTERNAL_HTTP,
            "upload_url": url,
            "token": token,
        })
        self.assertTrue(summary["configured"])
        self.assertTrue(summary["upload_url_configured"])
        self.assertTrue(summary["token_configured"])
        self.assertNotIn(url, repr(summary))
        self.assertNotIn(token, repr(summary))
        stored = get_reference_relay_config()
        self.assertEqual(stored["upload_url"], url)
        self.assertEqual(stored["token"], token)

    def test_metadata_endpoint_is_rejected(self) -> None:
        for url in (
            "http://169.254.169.254/latest/meta-data",
            "http://100.100.100.200/latest/meta-data",
            "http://metadata.google.internal/computeMetadata/v1",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ReferenceRelayConfigError):
                    self.service.update_config({"mode": EXTERNAL_HTTP, "upload_url": url})

    def test_query_fragment_and_non_http_are_rejected(self) -> None:
        for url in (
            "https://relay.example.test/upload?token=secret",
            "https://relay.example.test/upload#fragment",
            "ftp://relay.example.test/upload",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ReferenceRelayConfigError):
                    self.service.update_config({"mode": EXTERNAL_HTTP, "upload_url": url})

    def test_token_can_be_cleared_without_echo(self) -> None:
        self.service.update_config({
            "mode": EXTERNAL_HTTP,
            "upload_url": "https://relay.example.test/upload",
            "token": "secret",
        })
        summary = self.service.update_config({"token": None})
        self.assertFalse(summary["token_configured"])
        self.assertIsNone(get_reference_relay_config()["token"])

    def test_disabling_preserves_endpoint_for_later_reenable_but_safe_summary_only(self) -> None:
        self.service.update_config({
            "mode": EXTERNAL_HTTP,
            "upload_url": "https://relay.example.test/upload",
            "token": "secret",
        })
        summary = self.service.update_config({"mode": DISABLED})
        self.assertEqual(summary["mode"], DISABLED)
        self.assertFalse(summary["configured"])
        self.assertTrue(summary["upload_url_configured"])
        self.assertTrue(summary["token_configured"])
        self.assertNotIn("relay.example.test", repr(summary))
        self.assertNotIn("secret", repr(summary))


if __name__ == "__main__":
    unittest.main()
