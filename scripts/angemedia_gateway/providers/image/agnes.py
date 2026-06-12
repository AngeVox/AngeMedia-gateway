"""Agnes image adapter."""
from __future__ import annotations

from typing import Any

from ... import config as C
from ...media import openai_image_response
from ...schemas import ImageRequest
from ..base import RouteTarget
from ..errors import BackendUnavailable, ProviderProtocolError
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping


AGNES_IMAGE_EXTRA_ALLOWLIST = {
    "image", "images", "mask", "strength", "tags", "extra_body", "control_image", "reference_image",
    "reference_images", "input_image", "input_images", "init_image", "mask_image", "edit_mode", "mode",
    "width", "height", "steps", "guidance_scale", "cfg_scale", "sampler", "scheduler",
}


def extract_extra_image_options(req: ImageRequest) -> dict[str, Any]:
    extras = getattr(req, "model_extra", {}) or {}
    return {key: value for key, value in extras.items() if key in AGNES_IMAGE_EXTRA_ALLOWLIST and value is not None}


def has_agnes_image_input(payload: dict[str, Any]) -> bool:
    direct_keys = ("image", "images", "input_image", "input_images", "init_image", "mask", "mask_image", "control_image", "reference_image", "reference_images")
    if any(payload.get(key) for key in direct_keys):
        return True
    extra_body = payload.get("extra_body")
    return isinstance(extra_body, dict) and any(extra_body.get(key) for key in direct_keys)


class AgnesImageProvider:
    name = "agnes_image"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        if not C.AGNES_API_KEY:
            raise BackendUnavailable("AGNES_API_KEY is not configured")

        payload: dict[str, Any] = {
            "model": target.model,
            "prompt": req.prompt,
            "n": req.n,
            "size": req.size,
        }
        extra_body: dict[str, Any] = {"response_format": req.response_format}
        if req.quality:
            extra_body["quality"] = req.quality
        if req.user:
            extra_body["user"] = req.user
        if req.safe is not None:
            extra_body["safe"] = req.safe
        if req.negative_prompt:
            extra_body["negative_prompt"] = req.negative_prompt
        if req.seed is not None:
            extra_body["seed"] = req.seed
        payload["extra_body"] = extra_body

        extra_options = extract_extra_image_options(req)
        user_extra_body = extra_options.pop("extra_body", None)
        payload.update(extra_options)
        if isinstance(user_extra_body, dict):
            payload.setdefault("extra_body", {}).update(user_extra_body)

        if has_agnes_image_input(payload):
            tags = payload.setdefault("tags", [])
            if isinstance(tags, str):
                tags = [tags]
                payload["tags"] = tags
            if isinstance(tags, list) and "img2img" not in tags:
                tags.append("img2img")

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
