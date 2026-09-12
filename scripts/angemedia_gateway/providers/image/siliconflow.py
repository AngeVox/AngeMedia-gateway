"""SiliconFlow image adapter."""
from __future__ import annotations

from typing import Any

from ... import config as C
from ...media import openai_image_response
from ...reference_images import (
    collect_image_reference_values,
    is_safe_image_data_url,
    materialize_image_reference,
)
from ...schemas import ImageRequest
from ...security import validate_provider_reference_url
from ..base import RouteTarget
from ..errors import BackendUnavailable
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping
from ..runtime_config import resolve_provider_runtime_config


QWEN_IMAGE_EDIT_2509 = "Qwen/Qwen-Image-Edit-2509"


def _provider_image_reference(value: str | None) -> str | None:
    try:
        materialized = materialize_image_reference(value)
    except ValueError as error:
        raise BackendUnavailable("SiliconFlow 本地参考图无法安全读取或格式不受支持") from error
    if not materialized or is_safe_image_data_url(materialized):
        return materialized
    try:
        return validate_provider_reference_url(materialized)
    except ValueError as error:
        raise BackendUnavailable("SiliconFlow 参考图必须是安全图片 data URL 或公开 http(s) URL") from error


def _qwen_edit_payload(req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
    references = [
        prepared
        for value in collect_image_reference_values(req)
        if (prepared := _provider_image_reference(value))
    ]
    if not references:
        raise BackendUnavailable("SiliconFlow Qwen Image Edit 需要至少一张参考图")
    if len(references) > 3:
        raise BackendUnavailable("SiliconFlow Qwen Image Edit 最多支持 3 张参考图")

    payload: dict[str, Any] = {
        "model": target.model,
        "prompt": req.prompt,
        "num_inference_steps": req.steps if req.steps is not None else 20,
    }
    if req.negative_prompt:
        payload["negative_prompt"] = req.negative_prompt
    if req.seed is not None:
        payload["seed"] = req.seed
    for field, value in zip(("image", "image2", "image3"), references, strict=False):
        payload[field] = value
    return payload


def build_siliconflow_image_payload(req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
    if target.model == QWEN_IMAGE_EDIT_2509:
        return _qwen_edit_payload(req, target)

    image_size = req.size if req.size in C.KOLORS_SIZES else "1024x1024"
    payload: dict[str, Any] = {
        "model": target.model,
        "prompt": req.prompt,
        "image_size": image_size,
        "batch_size": 1,
        "num_inference_steps": req.steps if req.steps is not None else 20,
        "guidance_scale": req.guidance if req.guidance is not None else 7.5,
    }
    if req.negative_prompt:
        payload["negative_prompt"] = req.negative_prompt
    if req.seed is not None:
        payload["seed"] = req.seed
    image = _provider_image_reference(req.image)
    if image:
        payload["image"] = image
    return payload


class SiliconFlowProvider:
    name = "siliconflow"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        runtime = resolve_provider_runtime_config(self.name)
        if not runtime.api_key:
            raise BackendUnavailable("SILICONFLOW_API_KEY is not configured")

        async with provider_client() as client:
            resp = await request_with_provider_errors(
                client,
                "POST",
                f"{runtime.base_url}/images/generations",
                provider="SiliconFlow",
                operation="generate",
                headers={
                    "Authorization": f"Bearer {runtime.api_key}",
                    "Content-Type": "application/json",
                },
                json=build_siliconflow_image_payload(req, target),
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
        return "configured" if resolve_provider_runtime_config(self.name).api_key else "not_configured"
