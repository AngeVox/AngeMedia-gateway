from __future__ import annotations

import asyncio
import base64
import unittest
from unittest.mock import patch

import httpx

from angemedia_gateway.providers.reference_delivery import RELAY_REQUIRED, ReferenceDeliveryDecision
from angemedia_gateway.providers.reference_relay import (
    ExternalHttpReferenceRelay,
    ReferenceRelayUnavailable,
    fulfill_relay_decision,
)


PNG = b"\x89PNG\r\n\x1a\nrelay-test"
PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")


class _Response:
    def __init__(self, status_code: int = 200, payload=None, *, json_error: Exception | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _Client:
    def __init__(self, response=None, *, error: Exception | None = None) -> None:
        self.response = response or _Response(payload={"url": "https://cdn.example.test/ref.png"})
        self.error = error
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        return self.response


class ExternalHttpReferenceRelayTest(unittest.TestCase):
    def test_data_url_upload_uses_multipart_ttl_and_optional_bearer(self) -> None:
        client = _Client(_Response(payload={
            "url": "https://cdn.example.test/ref.png",
            "expires_at": "2026-09-12T12:30:00Z",
        }))
        backend = ExternalHttpReferenceRelay(
            "http://192.168.1.10:8787/upload",
            token="relay-super-secret",
        )
        with patch("angemedia_gateway.providers.reference_relay.outbound_client", return_value=client):
            publication = asyncio.run(backend.publish_reference(PNG_DATA_URL, ttl_seconds=900))

        self.assertEqual(publication.url, "https://cdn.example.test/ref.png")
        self.assertEqual(publication.expires_at, "2026-09-12T12:30:00Z")
        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call["url"], "http://192.168.1.10:8787/upload")
        self.assertEqual(call["data"], {"ttl_seconds": "900"})
        self.assertEqual(call["headers"], {"Authorization": "Bearer relay-super-secret"})
        filename, content, mime = call["files"]["reference"]
        self.assertEqual(filename, "reference.png")
        self.assertEqual(content, PNG)
        self.assertEqual(mime, "image/png")

    def test_gateway_asset_is_materialized_before_upload(self) -> None:
        client = _Client()
        backend = ExternalHttpReferenceRelay("https://relay.example.test/upload")
        with (
            patch("angemedia_gateway.providers.reference_relay.materialize_gateway_image_reference", return_value=PNG_DATA_URL) as materialize,
            patch("angemedia_gateway.providers.reference_relay.outbound_client", return_value=client),
        ):
            asyncio.run(backend.publish_reference("/uploads/ref.png", ttl_seconds=600))
        materialize.assert_called_once_with("/uploads/ref.png")
        self.assertEqual(client.calls[0]["headers"], {})

    def test_remote_and_arbitrary_paths_are_not_uploaded(self) -> None:
        backend = ExternalHttpReferenceRelay("https://relay.example.test/upload")
        for reference in (
            "https://example.test/ref.png",
            "http://127.0.0.1/ref.png",
            "/etc/passwd",
            "file:///tmp/ref.png",
        ):
            with self.subTest(reference=reference):
                with self.assertRaises(ReferenceRelayUnavailable):
                    asyncio.run(backend.publish_reference(reference, ttl_seconds=600))

    def test_upstream_failures_do_not_leak_raw_body_or_token(self) -> None:
        secret = "relay-token-do-not-leak"
        cases = (
            _Client(_Response(status_code=500, payload={"secret": secret})),
            _Client(_Response(status_code=200, json_error=ValueError(f"raw body {secret}"))),
            _Client(_Response(status_code=200, payload={})),
            _Client(error=httpx.ConnectError(f"network {secret}")),
        )
        for client in cases:
            with self.subTest(client=client):
                backend = ExternalHttpReferenceRelay("https://relay.example.test/upload", token=secret)
                with patch("angemedia_gateway.providers.reference_relay.outbound_client", return_value=client):
                    with self.assertRaises(ReferenceRelayUnavailable) as ctx:
                        asyncio.run(backend.publish_reference(PNG_DATA_URL, ttl_seconds=600))
                rendered = str(ctx.exception)
                self.assertNotIn(secret, rendered)
                self.assertNotIn("raw body", rendered)

    def test_fulfill_still_validates_publication_url(self) -> None:
        backend = ExternalHttpReferenceRelay("https://relay.example.test/upload")
        client = _Client(_Response(payload={"url": "http://127.0.0.1/private.png"}))
        decision = ReferenceDeliveryDecision(
            kind=RELAY_REQUIRED,
            value=PNG_DATA_URL,
            source="data_url",
        )
        with patch("angemedia_gateway.providers.reference_relay.outbound_client", return_value=client):
            with self.assertRaisesRegex(ReferenceRelayUnavailable, "usable public URL"):
                asyncio.run(fulfill_relay_decision(decision, backend=backend, ttl_seconds=600))


if __name__ == "__main__":
    unittest.main()
