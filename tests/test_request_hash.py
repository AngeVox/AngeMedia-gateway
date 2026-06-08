"""request_hash canonical helper tests."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.request_hash import (  # noqa: E402
    canonicalize_request_hash_payload,
    compute_request_hash,
)


class RequestHashCanonicalTest(unittest.TestCase):
    def test_dict_key_order_is_stable(self) -> None:
        left = {"kind": "image", "params": {"size": "1024x1024", "seed": 7}}
        right = {"params": {"seed": 7, "size": "1024x1024"}, "kind": "image"}
        self.assertEqual(compute_request_hash(left), compute_request_hash(right))
        self.assertEqual(
            canonicalize_request_hash_payload(left),
            canonicalize_request_hash_payload(right),
        )

    def test_list_order_is_preserved(self) -> None:
        first = {"images": ["asset-a", "asset-b"]}
        second = {"images": ["asset-b", "asset-a"]}
        self.assertNotEqual(compute_request_hash(first), compute_request_hash(second))

    def test_hash_is_sha256_hex(self) -> None:
        digest = compute_request_hash({"kind": "video", "prompt": "cat"})
        self.assertEqual(len(digest), 64)
        self.assertRegex(digest, re.compile(r"^[0-9a-f]{64}$"))

    def test_canonical_payload_includes_version(self) -> None:
        canonical = canonicalize_request_hash_payload({"kind": "image"}, version=1)
        parsed = json.loads(canonical)
        self.assertEqual(parsed["request_hash_version"], 1)
        self.assertEqual(parsed["payload"], {"kind": "image"})

    def test_different_version_changes_hash(self) -> None:
        payload = {"kind": "image", "prompt": "cat"}
        self.assertNotEqual(
            compute_request_hash(payload, version=1),
            compute_request_hash(payload, version=2),
        )

    def test_missing_and_null_are_stable_and_distinct(self) -> None:
        missing = canonicalize_request_hash_payload({"kind": "image"})
        null = canonicalize_request_hash_payload({"kind": "image", "seed": None})
        self.assertNotEqual(missing, null)
        self.assertEqual(null, canonicalize_request_hash_payload({"seed": None, "kind": "image"}))

    def test_secret_like_key_fails_fast(self) -> None:
        forbidden_payloads = [
            {"api_key": "sk-secret"},
            {"nested": {"Authorization": "Bearer token"}},
            {"providerToken": "secret"},
            {"local_path": "D:/tmp/out.png"},
            {"key_hash": "abc"},
        ]
        for payload in forbidden_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    compute_request_hash(payload)

    def test_non_json_value_fails_fast(self) -> None:
        with self.assertRaises(TypeError):
            compute_request_hash({"kind": {"image"}})

    def test_non_string_key_fails_fast(self) -> None:
        with self.assertRaises(TypeError):
            compute_request_hash({1: "image"})  # type: ignore[dict-item]


if __name__ == "__main__":
    unittest.main()
