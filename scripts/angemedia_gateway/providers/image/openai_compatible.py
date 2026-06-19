"""OpenAI-compatible image adapter."""
from __future__ import annotations

from typing import Any

from ...schemas import ImageRequest
from ..base import RouteTarget
from ..errors import BackendUnavailable
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping
from ..runtime_config import resolve_provider_runtime_config


class OpenAICompatibleImageProvider:
    name = "openai_image"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        runtime = resolve_provider_runtime_config(self.name)
        if not runtime.api_key:
            raise BackendUnavailable("OPENAI_IMAGE_API_KEY / OPENAI_API_KEY is not configured")

        payload: dict[str, Any] = {
            "model": target.model,
            "prompt": req.prompt,
            "n": 1,
            "size": req.size,
            "response_format": req.response_format,
        }
        if req.quality:
            payload["quality"] = req.quality
        if req.user:
            payload["user"] = req.user

        async with provider_client() as client:
            resp = await request_with_provider_errors(
                client,
                "POST",
                f"{runtime.base_url}/images/generations",
                provider="OpenAI-compatible image",
                operation="generate",
                headers={
                    "Authorization": f"Bearer {runtime.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        data = require_mapping(
            safe_json_response(resp, provider="OpenAI-compatible image", operation="generate"),
            provider="OpenAI-compatible image",
            operation="generate",
        )
        items = data.get("data") or [{}]
        item = items[0] if isinstance(items, list) and items else {}
        if not isinstance(item, dict) or (not item.get("url") and not item.get("b64_json")):
            raise BackendUnavailable("OpenAI-compatible image provider 未返回图片数据")
        return data

    def health(self) -> str:
        return "configured" if resolve_provider_runtime_config(self.name).api_key else "not_configured"
