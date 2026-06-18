"""Materialize gateway-owned reference images for upstream providers."""
from __future__ import annotations

import base64
from pathlib import Path

from . import config as C


REFERENCE_IMAGE_MAX_BYTES = 20 * 1024 * 1024
_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


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
