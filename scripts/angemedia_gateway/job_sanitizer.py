"""Pure sanitization boundary for persisted job and queue metadata."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from .security import redact_secret_text

REDACTED = "[REDACTED]"
REDACTED_BINARY = "[REDACTED_BINARY]"
REDACTED_DATA_URL = "[REDACTED_DATA_URL]"
REDACTED_LOCAL_PATH = "[REDACTED_LOCAL_PATH]"
REDACTED_SIGNED_URL = "[REDACTED_SIGNED_URL]"
REDACTED_CREDENTIAL_URL = "[REDACTED_CREDENTIAL_URL]"

_SENSITIVE_KEYS = {
    "apikey", "authorization", "proxyauthorization", "token", "accesstoken",
    "refreshtoken", "bearertoken", "secret", "clientsecret", "password",
    "raw", "rawbody", "providerbody", "requestbody", "responsebody",
    "signedurl", "localpath", "filesystempath", "bytes", "binary",
    "imagebytes", "videobytes", "base64", "imagebase64", "videobase64",
}
_WINDOWS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_WINDOWS_PATH_ANY_RE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]+|\\\\)[^\s\"']+")
_REMOTE_URL_RE = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)
_CREDENTIAL_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"']*?@[^\s\"']+", re.IGNORECASE)
_DATA_URL_RE = re.compile(r"data:[^\s,]+,[^\s\"']+", re.IGNORECASE)
_BEARER_RE = re.compile(r"\bBearer\s+[^\s\"']+", re.IGNORECASE)


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _sanitize_string(value: str) -> str:
    text = redact_secret_text(_BEARER_RE.sub("Bearer ***REDACTED***", value))
    text = _DATA_URL_RE.sub(REDACTED_DATA_URL, text)
    text = _WINDOWS_PATH_ANY_RE.sub(REDACTED_LOCAL_PATH, text)
    text = _CREDENTIAL_URL_RE.sub(REDACTED_CREDENTIAL_URL, text)

    def redact_credential_url(match: re.Match[str]) -> str:
        return REDACTED_SIGNED_URL if urlsplit(match.group(0)).query else match.group(0)

    text = _REMOTE_URL_RE.sub(redact_credential_url, text)
    lowered = text.lower().strip()
    if lowered.startswith("data:"):
        return REDACTED_DATA_URL
    if lowered.startswith("file://") or _WINDOWS_PATH_RE.match(text.strip()):
        return REDACTED_LOCAL_PATH
    if text.startswith("/") and not text.startswith(("/uploads/", "/generated/")):
        return REDACTED_LOCAL_PATH
    parsed = urlsplit(text)
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc and parsed.query:
        # Keep neither credential-bearing query nor fragment. A marker avoids
        # accidentally turning a stripped signed URL into a reusable locator.
        return REDACTED_SIGNED_URL
    return text


def sanitize_job_value(value: Any, *, _depth: int = 0) -> Any:
    """Return bounded, JSON-serializable job metadata with secrets removed."""
    if _depth > 12:
        return REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return REDACTED_BINARY
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)[:128]
            if _normalized_key(key) in _SENSITIVE_KEYS:
                result[key] = REDACTED
            else:
                result[key] = sanitize_job_value(item, _depth=_depth + 1)
        return result
    if isinstance(value, Sequence):
        return [sanitize_job_value(item, _depth=_depth + 1) for item in value[:1000]]
    return _sanitize_string(str(value))


def sanitized_json(value: Any) -> str:
    return json.dumps(sanitize_job_value(value), ensure_ascii=False, separators=(",", ":"))


def sanitize_json_text(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return sanitized_json(_sanitize_string(str(value)))
    return sanitized_json(parsed)


def sanitize_error_text(value: str | None, *, limit: int = 1000) -> str | None:
    if value is None:
        return None
    return _sanitize_string(str(value))[:limit]
