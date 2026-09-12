"""Pollinations unified image API adapter with legacy public fallback."""
from __future__ import annotations

import base64
import time
import urllib.parse
from typing import Any

from ... import config as C
from ...media import openai_image_response
from ...outbound_http import outbound_client
from ...reference_images import (
    collect_image_reference_values,
    is_safe_image_data_url,
    materialize_image_reference,
)
from ...schemas import ImageRequest
from ...security import validate_provider_reference_url
from ..base import RouteTarget
from ..errors import BackendUnavailable, RateLimited
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import parse_size, require_mapping
from ..runtime_config import resolve_provider_runtime_config


def _pollinations_edit_references(req: ImageRequest) -> list[str]:
    prepared: list[str] = []
    for value in collect_image_reference_values(req):
        try:
            materialized = materialize_image_reference(value)
        except ValueError as exc:
            raise BackendUnavailable("Pollinations 参考图无法安全本地化") from exc
        if not materialized:
            continue
        if is_safe_image_data_url(materialized):
            prepared.append(materialized)
            continue
        try:
            prepared.append(validate_provider_reference_url(materialized))
        except ValueError as exc:
            raise BackendUnavailable("Pollinations 参考图必须是安全图片 data URL 或公开 http(s) URL") from exc
    return prepared


def _pollinations_json_result(response: Any, *, operation: str) -> dict[str, Any]:
    data = require_mapping(
        safe_json_response(response, provider="Pollinations", operation=operation),
        provider="Pollinations",
        operation=operation,
    )
    items = data.get("data")
    item = items[0] if isinstance(items, list) and items else None
    if not isinstance(item, dict) or not (item.get("url") or item.get("b64_json")):
        raise BackendUnavailable("Pollinations 未返回图片数据")
    return data


class PollinationsProvider:
    name = "pollinations"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        width, height = parse_size(req.size)
        runtime = resolve_provider_runtime_config(self.name)
        references = _pollinations_edit_references(req)
        wants_edit = req.operation == "edit" or bool(references)

        if runtime.api_key:
            if wants_edit:
                if not references:
                    raise BackendUnavailable("Pollinations 图片编辑需要参考图")
                payload: dict[str, Any] = {
                    "prompt": req.prompt,
                    "model": target.model,
                    "n": 1,
                    "image": [{"image_url": value} for value in references],
                }
                if req.size:
                    payload["size"] = f"{width}x{height}"
                if req.quality:
                    payload["quality"] = req.quality
                if req.safe is not None:
                    payload["safe"] = req.safe
                endpoint = f"{runtime.base_url}/images/edits"
                operation = "edit"
            else:
                payload = {
                    "prompt": req.prompt,
                    "model": target.model or C.POLLINATIONS_DEFAULT_MODEL,
                    "n": 1,
                    "size": f"{width}x{height}",
                    "response_format": req.response_format,
                }
                if req.quality:
                    payload["quality"] = req.quality
                if req.safe is not None:
                    payload["safe"] = req.safe
                endpoint = f"{runtime.base_url}/images/generations"
                operation = "generate"

            async with provider_client(timeout=C.HTTP_TIMEOUT) as client:
                resp = await request_with_provider_errors(
                    client,
                    "POST",
                    endpoint,
                    provider="Pollinations",
                    operation=operation,
                    headers={
                        "Authorization": f"Bearer {runtime.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            return _pollinations_json_result(resp, operation=operation)

        if wants_edit:
            raise BackendUnavailable("Pollinations 图片编辑需要配置 POLLINATIONS_API_KEY")

        encoded = urllib.parse.quote(req.prompt)
        query = {
            "width": str(width),
            "height": str(height),
            "model": target.model or C.POLLINATIONS_DEFAULT_MODEL,
            "nologo": "true",
        }
        if req.safe is not None:
            query["safe"] = str(req.safe).lower()
        legacy_url = f"https://image.pollinations.ai/prompt/{encoded}?{urllib.parse.urlencode(query)}"

        async with outbound_client(follow_redirects=True, timeout=C.HTTP_TIMEOUT) as client:
            resp = await client.get(legacy_url)
        content_type = resp.headers.get("content-type", "")
        if resp.status_code == 429:
            raise RateLimited("Pollinations rate limited")
        if resp.status_code != 200 or not content_type.startswith("image/"):
            raise BackendUnavailable(f"Pollinations legacy endpoint failed: {resp.status_code} {content_type}")

        ext = "png" if "png" in content_type else "jpg"
        filename = f"pollinations_{int(time.time() * 1000)}.{ext}"
        path = C.OUTPUT_DIR / filename
        path.write_bytes(resp.content)
        if req.response_format == "b64_json":
            return openai_image_response(b64_json=base64.b64encode(resp.content).decode("ascii"))
        return openai_image_response(url=f"{C.PUBLIC_BASE_URL}/generated/{filename}")

    def health(self) -> str:
        return "configured_key" if resolve_provider_runtime_config(self.name).api_key else "legacy_public_endpoint"
