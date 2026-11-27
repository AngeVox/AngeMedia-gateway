"""Materialize gateway-owned reference images for upstream providers."""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

from . import config as C
from .storage_files import storage_directory, storage_parts


REFERENCE_IMAGE_MAX_BYTES = 20 * 1024 * 1024
# 输入信封的容量；具体型号的业务数量上限仍由目录独立裁定。
MAX_IMAGE_REFERENCE_COUNT = 16
_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_ALLOWED_DATA_URL_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


class UnsafeImageReference(ValueError):
    """Reference is not a gateway-owned, materializable image."""


class FrozenImageReference:
    """引用快照放入自动删除的临时文件，按需编码，避免多图快照长期占用大块内存。"""

    def __init__(self, content: bytes):
        self._mime = _sniff_image_mime(content[:16])
        if not self._mime or not 0 < len(content) <= REFERENCE_IMAGE_MAX_BYTES:
            raise UnsafeImageReference('frozen reference is not a bounded image')
        self._size = len(content)
        self._lock = threading.Lock()
        self._stream = tempfile.TemporaryFile(prefix='angemedia-action-ref-')
        try:
            self._stream.write(content)
            self._stream.flush()
        except BaseException:
            self._stream.close()
            raise

    def data_url(self):
        with self._lock:
            self._stream.seek(0)
            content = self._stream.read(REFERENCE_IMAGE_MAX_BYTES + 1)
        if len(content) != self._size:
            raise UnsafeImageReference('frozen reference changed')
        return f'data:{self._mime};base64,' + base64.b64encode(content).decode('ascii')

    def close(self):
        with self._lock:
            self._stream.close()


def freeze_image_references(references, resources):
    """按已观察的摘要固定网关图片，供普通队列与已确认动作共用。"""
    from .media_execution_scope import MediaExecutionRejected
    result = {}
    for item in references:
        if item['kind'] != 'gateway_image':
            continue
        value = local_asset_to_data_url(item['locator'])
        if not value:
            raise MediaExecutionRejected('action_execution_reference_changed')
        content = base64.b64decode(value.partition(',')[2], validate=True)
        if len(content) != item['bytes'] or hashlib.sha256(content).hexdigest() != item['sha256']:
            raise MediaExecutionRejected('action_execution_reference_changed')
        frozen = FrozenImageReference(content)
        resources.callback(frozen.close)
        result[item['locator']] = frozen
        del value, content
    return result


def collect_image_reference_values(request: object) -> list[str]:
    """Collect ordered image references from the unified and legacy request fields."""
    values: list[str] = []
    for field in ("image", "reference_images", "images"):
        value = getattr(request, field, None)
        if value is None:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, str) and item.strip():
                values.append(item.strip())
    return values


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
    root, relative = selected
    try:
        parts = storage_parts(relative)
    except ValueError:
        raise UnsafeImageReference("reference image must use gateway storage") from None
    if Path(parts[-1]).suffix.lower() not in _ALLOWED_EXTENSIONS:
        raise UnsafeImageReference("reference image must be a supported image")
    if text.startswith("/generated/"):
        from .repositories.image_layers import generated_file_visible
        if not generated_file_visible(root.resolve().joinpath(*parts)):
            raise UnsafeImageReference("reference image is not available")
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
    from .media_execution_scope import current_execution_scope
    scope = current_execution_scope()
    if scope is not None:
        return scope.reference(gateway_path.strip())
    selected = _gateway_image_path(gateway_path)
    if selected is None:
        return None
    base_dir, relative = selected
    try:
        parts = storage_parts(relative)
        if Path(parts[-1]).suffix.lower() not in _ALLOWED_EXTENSIONS:
            return None
        root = base_dir.resolve()
        if gateway_path.strip().startswith("/generated/"):
            from .repositories.image_layers import generated_file_visible
            if not generated_file_visible(root.joinpath(*parts)):
                return None
        with storage_directory(root, parts[:-1]) as folder, folder.read(parts[-1]) as file_handle:
            size = os.fstat(file_handle.fileno()).st_size
            if not 0 < size <= REFERENCE_IMAGE_MAX_BYTES:
                return None
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
