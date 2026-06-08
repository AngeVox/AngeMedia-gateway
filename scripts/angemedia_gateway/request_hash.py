"""Stable request hash helpers for short-window generation dedupe."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


_DENIED_KEY_NAMES = {
    "api_key",
    "provider_api_key",
    "gateway_api_key",
    "authorization",
    "cookie",
    "session",
    "password",
    "secret",
    "token",
    "key_hash",
    "base_url",
    "local_path",
    "file_path",
    "raw_file_path",
    "timestamp",
    "uuid",
    "request_id",
    "raw_response",
    "raw_error_body",
    "raw_provider_response",
    "stack_trace",
}


def _normalized_key_name(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")


def _assert_safe_key(key: str) -> None:
    normalized = _normalized_key_name(key)
    compact = normalized.replace("_", "")
    if normalized in _DENIED_KEY_NAMES:
        raise ValueError(f"request hash payload contains forbidden key: {key}")
    if normalized.endswith(("_api_key", "_token", "_secret", "_password", "_session", "_cookie")):
        raise ValueError(f"request hash payload contains forbidden key: {key}")
    if compact.endswith(("apikey", "token", "secret", "password", "session", "cookie")):
        raise ValueError(f"request hash payload contains forbidden key: {key}")
    if compact in {"baseurl", "localpath", "filepath", "requestid", "keyhash"}:
        raise ValueError(f"request hash payload contains forbidden key: {key}")


def _normalize_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("request hash payload keys must be strings")
            _assert_safe_key(key)
            normalized[key] = _normalize_json_value(item)
        return normalized
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    raise TypeError(f"request hash payload contains non-JSON value: {type(value).__name__}")


def canonicalize_request_hash_payload(payload: Mapping[str, Any], version: int = 1) -> str:
    """Return canonical JSON for an already-sanitized request hash payload."""
    if version < 1:
        raise ValueError("request hash version must be >= 1")
    normalized = _normalize_json_value(payload)
    wrapped = {
        "request_hash_version": int(version),
        "payload": normalized,
    }
    return json.dumps(wrapped, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_request_hash(payload: Mapping[str, Any], version: int = 1) -> str:
    """Compute a SHA-256 hex digest for an already-sanitized request hash payload."""
    canonical = canonicalize_request_hash_payload(payload, version=version)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
