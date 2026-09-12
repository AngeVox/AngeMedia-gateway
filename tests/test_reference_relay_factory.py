from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import angemedia_gateway.config as C
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.providers.reference_relay import ExternalHttpReferenceRelay, configured_reference_relay_backend
from angemedia_gateway.services.reference_relay_config import ReferenceRelayConfigService


class ReferenceRelayFactoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="relay-factory-")
        self._original_db = C.DB_FILE
        C.DB_FILE = Path(self._tmp.name) / "angemedia.db"
        init_db()
        self.service = ReferenceRelayConfigService()

    def tearDown(self) -> None:
        C.DB_FILE = self._original_db
        self._tmp.cleanup()

    def test_disabled_returns_none(self) -> None:
        self.assertIsNone(configured_reference_relay_backend())

    def test_external_http_builds_backend(self) -> None:
        self.service.update_config({
            "mode": "external_http",
            "upload_url": "http://192.168.1.10:8787/upload",
            "token": "test-token",
        })
        backend = configured_reference_relay_backend()
        self.assertIsInstance(backend, ExternalHttpReferenceRelay)
        self.assertEqual(backend.upload_url, "http://192.168.1.10:8787/upload")
        self.assertEqual(backend.token, "test-token")


if __name__ == "__main__":
    unittest.main()
