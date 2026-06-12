"""SiliconFlow image adapter."""
from __future__ import annotations

from typing import Any

from ... import config as C
from ...media import openai_image_response
from ...schemas import ImageRequest
from ..base import RouteTarget
from ..errors import BackendUnavailable
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping


class SiliconFlowProvider:
    name = "siliconflow"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        if not C.SILICONFLOW_API_KEY:
            raise BackendUnavailable("SILICONFLOW_API_KEY is not configured")

        image_size = req.size if req.size in C.KOLORS_SIZES else "1024x1024"
        payload = {
            "model": target.model,
            "prompt": req.prompt,
            "image_size": image_size,
            "batch_size": 1,
            "num_inference_steps": 20,
            "guidance_scale": 7.5,
        }

        async with provider_client() as client:
            resp = await request_with_provider_errors(
                client,
                "POST",
                "https://api.siliconflow.cn/v1/images/generations",
                provider="SiliconFlow",
                operation="generate",
                headers={
                    "Authorization": f"Bearer {C.SILICONFLOW_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        data = require_mapping(
            safe_json_response(resp, provider="SiliconFlow", operation="generate"),
            provider="SiliconFlow",
            operation="generate",
        )
        images = data.get("images") or []
        if not images or not images[0].get("url"):
            raise BackendUnavailable("SiliconFlow 未返回图片地址")
        return openai_image_response(url=images[0]["url"])

    def health(self) -> str:
        return "configured" if C.SILICONFLOW_API_KEY else "not_configured"
