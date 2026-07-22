"""Agnes image adapter."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...media import openai_image_response
from ...reference_images import materialize_image_reference
from ...schemas import ImageRequest
from ..base import RouteTarget
from ..errors import BackendUnavailable, ProviderProtocolError
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping
from ..runtime_config import resolve_provider_runtime_config


AGNES_SEED_MODELS = frozenset({"agnes-image-2.0-flash"})
AGNES_RATIO_MODELS = frozenset({"agnes-image-2.1-flash"})


def _reference_images(req: ImageRequest) -> list[str]:
    values: list[Any] = []
    if req.image:
        values.append(req.image)
    images = getattr(req, "images", None)
    if isinstance(images, Iterable) and not isinstance(images, (str, bytes, dict)):
        values.extend(images)
    references: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            materialized = materialize_image_reference(value)
        except ValueError as error:
            raise BackendUnavailable("Agnes Image local reference cannot be safely materialized") from error
        if materialized:
            references.append(materialized)
    return references


def build_agnes_image_payload(req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": target.model,
        "prompt": req.prompt,
        "size": req.size,
    }
    if target.model in AGNES_RATIO_MODELS and req.aspect_ratio:
        payload["ratio"] = req.aspect_ratio
    if target.model in AGNES_SEED_MODELS and req.seed is not None:
        payload["seed"] = req.seed

    references = _reference_images(req)
    if references:
        payload["extra_body"] = {
            "image": references,
            "response_format": req.response_format,
        }
    elif req.response_format == "b64_json":
        payload["return_base64"] = True
    else:
        payload["extra_body"] = {"response_format": "url"}
    return payload


class AgnesImageProvider:
    name = "agnes_image"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        runtime = resolve_provider_runtime_config(self.name)
        if not runtime.api_key:
            raise BackendUnavailable("AGNES_API_KEY is not configured")

        payload = build_agnes_image_payload(req, target)

        async with provider_client() as client:
            resp = await request_with_provider_errors(
                client,
                "POST",
                f"{runtime.base_url}/images/generations",
                provider="Agnes Image",
                operation="generate",
                ok_statuses=(200, 201),
                headers={
                    "Authorization": f"Bearer {runtime.api_key}",
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
        return "configured" if resolve_provider_runtime_config(self.name).api_key else "not_configured"


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
