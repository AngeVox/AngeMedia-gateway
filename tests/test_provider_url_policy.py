"""Configured Provider endpoint policy contracts."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.providers.endpoint_policy import (  # noqa: E402
    validate_provider_base_url,
    validate_provider_probe_url,
)


class ProviderUrlPolicyTest(unittest.TestCase):
    def test_base_url_allows_admin_configured_local_and_overlay_endpoints(self) -> None:
        allowed = (
            "http://localhost:3000/v1",
            "http://127.0.0.1:3000/v1",
            "http://10.0.0.2:3000/v1",
            "http://172.16.8.2:3000/v1",
            "http://192.168.1.10:3000/v1",
            "http://100.64.0.5:3000/v1",
            "http://[::1]:3000/v1",
            "http://[fd00::10]:3000/v1",
            "http://new-api.lan:3000/v1",
            "https://relay.example.com/v1",
        )
        for value in allowed:
            with self.subTest(value=value):
                self.assertEqual(validate_provider_base_url(value), value)

    def test_probe_url_allows_local_endpoint_and_query(self) -> None:
        value = "http://192.168.1.10:3000/status?format=json"
        self.assertEqual(validate_provider_probe_url(value), value)

    def test_rejects_metadata_link_local_multicast_and_unspecified_targets(self) -> None:
        rejected = (
            "http://169.254.169.254/latest/meta-data",
            "http://100.100.100.200/latest/meta-data",
            "http://[fe80::1]/v1",
            "http://224.0.0.1/v1",
            "http://0.0.0.0:3000/v1",
            "http://[::]:3000/v1",
            "http://metadata.google.internal/v1",
            "http://[::ffff:169.254.169.254]/v1",
            "http://[::ffff:100.100.100.200]/latest/meta-data",
        )
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_provider_base_url(value)

    def test_rejects_userinfo_bad_scheme_query_fragment_and_generation_path(self) -> None:
        rejected = (
            "ftp://192.168.1.10/v1",
            "http://user:pass@192.168.1.10:3000/v1",
            "http://192.168.1.10:3000/v1?x=1",
            "http://192.168.1.10:3000/v1#fragment",
            "http://192.168.1.10:3000/v1/images/generations",
        )
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_provider_base_url(value)

    def test_does_not_resolve_hostnames_while_saving(self) -> None:
        self.assertEqual(
            validate_provider_base_url("http://split-horizon.internal:3000/v1"),
            "http://split-horizon.internal:3000/v1",
        )


if __name__ == "__main__":
    unittest.main()
