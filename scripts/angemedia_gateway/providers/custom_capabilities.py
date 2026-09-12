"""Conservative capability declarations for custom OpenAI-compatible image providers."""
from __future__ import annotations

import json
from typing import Any, Mapping

from ..reference_images import collect_image_reference_values
from ..schemas import ImageRequest


DEFAULT_CUSTOM_IMAGE_CAPABILITIES: dict[str, Any] = {
    "text_to_image": True,
    "image_edit": False,
    "max_reference_images": 1,
    "supports_mask": False,
}
_ALLOWED_KEYS = {"image_edit", "max_reference_images", "supports_mask", "text_to_image"}


def normalize_custom_image_capabilities(value: Any) -> dict[str, Any]:
    """Return a safe declaration; malformed persisted data falls back to T2I-only."""
    raw: Any = value
    if isinstance(value, str):
        try:
            raw = json.loads(value) if value.strip() else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
    if not isinstance(raw, Mapping):
        raw = {}

    image_edit = raw.get("image_edit") is True
    try:
        max_refs = int(raw.get("max_reference_images", 1))
    except (TypeError, ValueError):
        max_refs = 1
    max_refs = max(1, min(10, max_refs))
    supports_mask = image_edit and raw.get("supports_mask") is True
    return {
        "text_to_image": True,
        "image_edit": image_edit,
        "max_reference_images": max_refs,
        "supports_mask": supports_mask,
    }


def validate_custom_image_capability_declaration(value: Any) -> dict[str, Any]:
    """Validate administrator-supplied capability flags before persistence."""
    if value is None:
        return dict(DEFAULT_CUSTOM_IMAGE_CAPABILITIES)
    if not isinstance(value, Mapping):
        raise ValueError("capabilities must be an object")
    unknown = set(value) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(f"unsupported custom provider capability: {sorted(unknown)[0]}")
    if "text_to_image" in value and value.get("text_to_image") is not True:
        raise ValueError("custom openai_image providers must keep text_to_image enabled")
    for key in ("image_edit", "supports_mask"):
        if key in value and not isinstance(value[key], bool):
            raise ValueError(f"{key} must be a boolean")
    if "max_reference_images" in value:
        raw_max = value["max_reference_images"]
        if isinstance(raw_max, bool) or not isinstance(raw_max, int) or not 1 <= raw_max <= 10:
            raise ValueError("max_reference_images must be an integer between 1 and 10")
    normalized = normalize_custom_image_capabilities(value)
    if normalized["supports_mask"] and not normalized["image_edit"]:
        raise ValueError("supports_mask requires image_edit")
    return normalized


def custom_image_capabilities_json(value: Any) -> str:
    normalized = validate_custom_image_capability_declaration(value)
    return json.dumps(normalized, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def validate_custom_image_request(req: ImageRequest, provider: Mapping[str, Any]) -> dict[str, Any]:
    """Reject richer operations unless the custom provider explicitly opted in."""
    capabilities = normalize_custom_image_capabilities(provider.get("capabilities"))
    references = collect_image_reference_values(req)
    wants_edit = req.operation == "edit" or bool(references) or bool(req.mask)
    if not wants_edit:
        return capabilities
    if not capabilities["image_edit"]:
        raise ValueError("custom provider does not declare image editing support")
    if not references:
        raise ValueError("custom image editing requires at least one reference image")
    if len(references) > capabilities["max_reference_images"]:
        raise ValueError(
            f"custom provider supports at most {capabilities['max_reference_images']} reference images"
        )
    if req.mask and not capabilities["supports_mask"]:
        raise ValueError("custom provider does not declare mask support")
    return capabilities
