"""Agnes image adapter."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ... import config as C
from ...media import openai_image_response
from ...schemas import ImageRequest
from ..base import RouteTarget
from ..errors import BackendUnavailable, ProviderProtocolError
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping


AGNES_SEED_MODELS = frozenset({"agnes-image-2.0-flash"})


def _reference_urls(req: ImageRequest) -> list[str]:
    values: list[Any] = []
    if req.image:
        values.append(req.image)
    images = getattr(req, "images", None)
    if isinstance(images, Iterable) and not isinstance(images, (str, bytes, dict)):
        values.extend(images)
    return [value.strip() for value in values if isinstance(value, str) and value.strip()]


def build_agnes_image_payload(req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": target.model,
        "prompt": req.prompt,
        "size": req.size,
        "extra_body": {"response_format": "url"},
    }
    if target.model in AGNES_SEED_MODELS and req.seed is not None:
        payload["seed"] = req.seed

    references = _reference_urls(req)
    if references:
        payload["tags"] = ["img2img"]
        payload["extra_body"]["image"] = references
    return payload


class AgnesImageProvider:
    name = "agnes_image"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        if not C.AGNES_API_KEY:
            raise BackendUnavailable("AGNES_API_KEY is not configured")

        payload = build_agnes_image_payload(req, target)

        async with provider_client() as client:
            resp = await request_with_provider_errors(
                client,
                "POST",
                f"{C.AGNES_BASE_URL}/images/generations",
                provider="Agnes Image",
                operation="generate",
                ok_statuses=(200, 201),
                headers={
                    "Authorization": f"Bearer {C.AGNES_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        data = require_mapping(
            safe_json_response(resp, provider="Agnes Image", operation="generate"),
            provider="Agnes Image",
            operation="generate",
        )
        return normalize_image_response(data)

    def health(self) -> str:
        return "configured" if C.AGNES_API_KEY else "not_configured"


def normalize_image_response(data: dict[str, Any]) -> dict[str, Any]:
    if isinstance(data.get("data"), list) and data["data"]:
        item = data["data"][0]
        if isinstance(item, dict):
            if item.get("url") or item.get("b64_json"):
                return data
            for key in ("image_url", "output_url"):
                if item.get(key):
                    return openai_image_response(url=item[key])
        if isinstance(item, str):
            return openai_image_response(url=item)

    for key in ("url", "image_url", "output_url"):
        if isinstance(data.get(key), str):
            return openai_image_response(url=data[key])

    images = data.get("images") or data.get("output_images") or []
    if images:
        first = images[0]
        if isinstance(first, str):
            return openai_image_response(url=first)
        if isinstance(first, dict):
            for key in ("url", "image_url", "output_url", "b64_json"):
                if first.get(key):
                    if key == "b64_json":
                        return openai_image_response(b64_json=first[key])
                    return openai_image_response(url=first[key])

    raise ProviderProtocolError("Agnes Image generate failed: protocol")
