"""ByteDance Seedream text-to-image adapter."""
from __future__ import annotations

from typing import Any

from ...schemas import ImageRequest
from ..base import RouteTarget
from ..errors import BackendUnavailable, ProviderProtocolError
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping
from ..runtime_config import resolve_provider_runtime_config


def build_bytedance_image_payload(req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
    """Build the confirmed Seedream 3.0 text-to-image request fields."""

    payload: dict[str, Any] = {
        "model": target.model,
        "prompt": req.prompt,
        "size": req.size,
        "response_format": "b64_json",
    }
    if req.seed is not None:
        payload["seed"] = req.seed
    return payload


class ByteDanceImageProvider:
    name = "bytedance"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        runtime = resolve_provider_runtime_config(self.name)
        if not runtime.api_key:
            raise BackendUnavailable("BYTEDANCE_API_KEY is not configured")

        async with provider_client() as client:
            response = await request_with_provider_errors(
                client,
                "POST",
                f"{runtime.base_url}/images/generations",
                provider="ByteDance Seedream",
                operation="generate",
                headers={
                    "Authorization": f"Bearer {runtime.api_key}",
                    "Content-Type": "application/json",
                },
                json=build_bytedance_image_payload(req, target),
            )

        data = require_mapping(
            safe_json_response(response, provider="ByteDance Seedream", operation="generate"),
            provider="ByteDance Seedream",
            operation="generate",
        )
        items = data.get("data")
        item = items[0] if isinstance(items, list) and items else None
        if not isinstance(item, dict):
            raise ProviderProtocolError("ByteDance Seedream generate failed: missing image data")
        if isinstance(item.get("b64_json"), str) and item["b64_json"]:
            return {"data": [{"b64_json": item["b64_json"]}]}
        if isinstance(item.get("url"), str) and item["url"]:
            return {"data": [{"url": item["url"]}]}
        raise ProviderProtocolError("ByteDance Seedream generate failed: missing image output")

    def health(self) -> str:
        return "configured" if resolve_provider_runtime_config(self.name).api_key else "not_configured"
