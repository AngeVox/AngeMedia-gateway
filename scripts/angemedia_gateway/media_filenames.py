"""Media filename and extension helpers."""
from __future__ import annotations

import hashlib
import mimetypes
import re
import urllib.parse
from pathlib import Path
from typing import Optional


def extension_from_response(url: str, content_type: str, fallback_ext: str) -> str:
    content_type = content_type.split(";", 1)[0].strip().lower()
    by_type = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
    }
    if content_type in by_type:
        return by_type[content_type]
    suffix = Path(urllib.parse.urlparse(url).path).suffix
    if suffix and len(suffix) <= 8:
        return suffix
    if content_type in {"application/octet-stream", "binary/octet-stream"}:
        return fallback_ext if fallback_ext.startswith(".") else f".{fallback_ext}"
    guessed = mimetypes.guess_extension(content_type) if content_type else None
    if guessed:
        return guessed
    return fallback_ext if fallback_ext.startswith(".") else f".{fallback_ext}"


def safe_filename_prefix(prefix: str) -> str:
    parts = [
        part.lower()
        for part in re.split(r"[^a-zA-Z0-9]+", prefix)
        if part.strip()
    ]
    collapsed: list[str] = []
    for part in parts:
        if collapsed and collapsed[-1] == part:
            continue
        collapsed.append(part)
    if len(collapsed) >= 3 and collapsed[0] == collapsed[-1]:
        collapsed.pop()
    return "-".join(collapsed) or "media"


def stable_filename(prefix: str, url: str, ext: str, stable_id: Optional[str] = None) -> str:
    digest = hashlib.sha256((stable_id or url).encode("utf-8")).hexdigest()[:16]
    safe_prefix = safe_filename_prefix(prefix)
    return f"{safe_prefix}-{digest}{ext}"
