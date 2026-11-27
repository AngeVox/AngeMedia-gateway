"""OpenAI-compatible media response helpers."""
from __future__ import annotations

import base64
import binascii
from typing import Any

from .svg_output import SVG_MAX_BYTES, validate_static_svg


IMAGE_B64_MAX_BYTES = 25 * 1024 * 1024


def _decode_base64(value: Any, max_bytes: int) -> bytes:
    if not isinstance(value, str) or not value:
        raise RuntimeError("后端没有返回有效的 b64_json")
    if len(value) > ((max_bytes + 2) // 3) * 4:
        raise RuntimeError("Base64 图片超过本地化大小限制")
    try:
        content = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("后端返回的 Base64 图片无效") from exc
    if not content or len(content) > max_bytes:
        raise RuntimeError("Base64 图片超过本地化大小限制")
    return content


def decode_svg_b64(value: Any, *, max_bytes: int = SVG_MAX_BYTES) -> bytes:
    content = _decode_base64(value, min(max_bytes, SVG_MAX_BYTES))
    validate_static_svg(content)
    return content


def decode_image_b64(value: Any, *, max_bytes: int = IMAGE_B64_MAX_BYTES) -> tuple[bytes, str]:
    """有界解码生成图片并按真实文件头定格式，适配器和本地化共用。"""
    content = _decode_base64(value, max_bytes)
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return content, ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return content, ".jpg"
    if content.startswith(b"RIFF") and len(content) >= 12 and content[8:12] == b"WEBP":
        return content, ".webp"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return content, ".gif"
    raise RuntimeError("后端返回的 Base64 数据不是受支持的图片")


def openai_image_response(*, url: str | None = None, b64_json: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {}
    if url:
        item["url"] = url
    if b64_json:
        item["b64_json"] = b64_json
    return {"created": 0, "data": [item]}
