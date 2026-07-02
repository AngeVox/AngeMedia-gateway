"""OpenAI-compatible media response helpers."""
from __future__ import annotations

from typing import Any


def openai_image_response(*, url: str | None = None, b64_json: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {}
    if url:
        item["url"] = url
    if b64_json:
        item["b64_json"] = b64_json
    return {"created": 0, "data": [item]}
