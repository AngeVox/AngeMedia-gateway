"""OpenAI Image API adapter."""
from __future__ import annotations

import base64
import binascii
from typing import Any

from ...reference_images import (
    collect_image_reference_values,
    is_safe_image_data_url,
    materialize_gateway_image_reference,
)
from ...schemas import ImageRequest
from ..base import RouteTarget
from ..errors import BackendUnavailable
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping
from ..runtime_config import resolve_provider_runtime_config


_IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _openai_file_part(value: str, index: int, *, prefix: str = "image") -> tuple[str, bytes, str]:
    text = str(value or "").strip()
    if text.startswith(("/uploads/", "/generated/")):
        text = materialize_gateway_image_reference(text)
    if not is_safe_image_data_url(text):
        raise BackendUnavailable("OpenAI image references must be gateway assets or safe image data URLs")
    header, _, encoded = text.partition(",")
    mime = header[5:-7].lower()
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BackendUnavailable("OpenAI image reference could not be decoded") from exc
    extension = _IMAGE_EXTENSIONS.get(mime)
    if not extension:
        raise BackendUnavailable("OpenAI image reference format is not supported")
    return f"{prefix}-{index}{extension}", content, mime


def _is_edit_request(req: ImageRequest) -> bool:
    return bool(
        req.operation == "edit"
        or req.mask
        or collect_image_reference_values(req)
    )


class OpenAICompatibleImageProvider:
    """Built-in OpenAI Image API provider.

    Custom OpenAI-compatible providers intentionally use a separate conservative
    adapter so OpenAI-specific edit/multipart behavior does not leak into them.
    """

    name = "openai_image"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        runtime = resolve_provider_runtime_config(self.name)
        if not runtime.api_key:
            raise BackendUnavailable("OPENAI_IMAGE_API_KEY / OPENAI_API_KEY is not configured")

        if _is_edit_request(req):
            data = await self._edit(req, target, runtime.api_key, runtime.base_url)
        else:
            data = await self._generate(req, target, runtime.api_key, runtime.base_url)
        return self._validated_response(data)

    async def _generate(
        self,
        req: ImageRequest,
        target: RouteTarget,
        api_key: str,
        base_url: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": target.model,
            "prompt": req.prompt,
            "n": 1,
            "size": req.size,
        }
        if req.quality:
            payload["quality"] = req.quality
        if req.user:
            payload["user"] = req.user

        async with provider_client() as client:
            response = await request_with_provider_errors(
                client,
                "POST",
                f"{base_url}/images/generations",
                provider="OpenAI image",
                operation="generate",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        return require_mapping(
            safe_json_response(response, provider="OpenAI image", operation="generate"),
            provider="OpenAI image",
            operation="generate",
        )

    async def _edit(
        self,
        req: ImageRequest,
        target: RouteTarget,
        api_key: str,
        base_url: str,
    ) -> dict[str, Any]:
        references = collect_image_reference_values(req)
        if not references:
            raise BackendUnavailable("OpenAI image edit requires at least one reference image")

        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for index, reference in enumerate(references, start=1):
            files.append(("image[]", _openai_file_part(reference, index)))
        if req.mask:
            files.append(("mask", _openai_file_part(req.mask, 1, prefix="mask")))

        form: dict[str, str] = {
            "model": target.model,
            "prompt": req.prompt,
            "n": "1",
            "size": req.size,
        }
        if req.quality:
            form["quality"] = req.quality
        if req.user:
            form["user"] = req.user

        async with provider_client() as client:
            response = await request_with_provider_errors(
                client,
                "POST",
                f"{base_url}/images/edits",
                provider="OpenAI image",
                operation="edit",
                headers={"Authorization": f"Bearer {api_key}"},
                data=form,
                files=files,
            )
        return require_mapping(
            safe_json_response(response, provider="OpenAI image", operation="edit"),
            provider="OpenAI image",
            operation="edit",
        )

    @staticmethod
    def _validated_response(data: dict[str, Any]) -> dict[str, Any]:
        items = data.get("data") or [{}]
        item = items[0] if isinstance(items, list) and items else {}
        if not isinstance(item, dict) or (not item.get("url") and not item.get("b64_json")):
            raise BackendUnavailable("OpenAI image provider did not return image data")
        return data

    def health(self) -> str:
        return "configured" if resolve_provider_runtime_config(self.name).api_key else "not_configured"
