"""Materialize gateway-owned reference images for upstream providers."""
from __future__ import annotations

import base64
import binascii
from pathlib import Path
from urllib.parse import urlparse

from . import config as C


REFERENCE_IMAGE_MAX_BYTES = 20 * 1024 * 1024
_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_ALLOWED_DATA_URL_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


class UnsafeImageReference(ValueError):
    """Reference is not a gateway-owned, materializable image."""


def validate_gateway_image_reference(value: str | None) -> str:
    """Accept only image paths served from the gateway's protected storage."""
    if not isinstance(value, str):
        raise UnsafeImageReference("reference image must use gateway storage")
    text = value.strip()
    parsed = urlparse(text)
    if (
        not text
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or "\\" in text
        or "%" in text
    ):
        raise UnsafeImageReference("reference image must use gateway storage")
    selected = _gateway_image_path(text)
    if selected is None:
        raise UnsafeImageReference("reference image must use gateway storage")
    _, relative = selected
    parts = relative.split("/")
    if not relative or any(part in {"", ".", ".."} for part in parts):
        raise UnsafeImageReference("reference image must use gateway storage")
    if Path(relative).suffix.lower() not in _ALLOWED_EXTENSIONS:
        raise UnsafeImageReference("reference image must be a supported image")
    return text


def _sniff_image_mime(header: bytes) -> str | None:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WEBP":
        return "image/webp"
    return None


def _gateway_image_path(gateway_path: str) -> tuple[Path, str] | None:
    text = gateway_path.strip()
    if text.startswith("/generated/"):
        return C.OUTPUT_DIR, text[len("/generated/"):]
    if text.startswith("/uploads/"):
        return C.UPLOAD_DIR, text[len("/uploads/"):]
    return None


def local_asset_to_data_url(gateway_path: str | None) -> str | None:
    """Convert a safe gateway image path to a size-limited data URL."""
    if not isinstance(gateway_path, str) or not gateway_path.strip():
        return None
    selected = _gateway_image_path(gateway_path)
    if selected is None:
        return None
    base_dir, relative = selected
    if not relative:
        return None

    try:
        resolved = (base_dir / relative).resolve()
        resolved.relative_to(base_dir.resolve())
        if not resolved.is_file() or resolved.suffix.lower() not in _ALLOWED_EXTENSIONS:
            return None
        if resolved.stat().st_size <= 0 or resolved.stat().st_size > REFERENCE_IMAGE_MAX_BYTES:
            return None
        with resolved.open("rb") as file_handle:
            content = file_handle.read(REFERENCE_IMAGE_MAX_BYTES + 1)
    except (OSError, ValueError):
        return None

    if not content or len(content) > REFERENCE_IMAGE_MAX_BYTES:
        return None
    mime = _sniff_image_mime(content[:16])
    if mime is None:
        return None
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def is_safe_image_data_url(value: str) -> bool:
    """Return whether a value is a size-limited base64 image data URL."""
    if not isinstance(value, str) or not value.startswith("data:image/"):
        return False
    header, separator, encoded = value.partition(",")
    if not separator or not header.lower().endswith(";base64"):
        return False
    mime = header[5:-7].lower()
    if mime not in _ALLOWED_DATA_URL_MIME_TYPES:
        return False
    max_encoded_length = ((REFERENCE_IMAGE_MAX_BYTES + 2) // 3) * 4
    if not encoded or len(encoded) > max_encoded_length:
        return False
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return False
    return 0 < len(content) <= REFERENCE_IMAGE_MAX_BYTES and _sniff_image_mime(content[:16]) == mime


def materialize_image_reference(value: str | None) -> str | None:
    """Convert gateway-owned image paths to data URLs and preserve remote/data URL inputs."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.startswith(("/uploads/", "/generated/")):
        data_url = local_asset_to_data_url(text)
        if not data_url:
            raise ValueError("local reference image cannot be safely materialized")
        return data_url
    return text


def materialize_gateway_image_reference(value: str | None) -> str:
    """Convert a validated gateway-owned image path to a bounded data URL."""
    safe_path = validate_gateway_image_reference(value)
    data_url = local_asset_to_data_url(safe_path)
    if not data_url:
        raise UnsafeImageReference("reference image cannot be safely materialized")
    return data_url
