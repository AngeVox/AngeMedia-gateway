"""Pollinations认证图片接口；复用原JSON生成/编辑与媒体交付链路。"""
from __future__ import annotations

from typing import Any

from ... import config as C
from ...media_url_policy import is_http_url
from ...reference_images import collect_image_reference_values
from ...schemas import ImageRequest
from ..base import RouteTarget
from ..catalog.validation import validate_image_operation_request
from ..errors import BackendUnavailable, ProviderProtocolError
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import parse_size, require_mapping
from ..runtime_config import resolve_provider_runtime_config
from ..reference_delivery import DATA_URL, PUBLIC_URL, RELAY_REQUIRED, prepare_builtin_reference


def _pollinations_edit_references(req: ImageRequest) -> list[str]:
    prepared: list[str] = []
    for value in collect_image_reference_values(req):
        try:
            decision = prepare_builtin_reference("pollinations", value)
        except ValueError as exc:
            raise BackendUnavailable("Pollinations 参考图格式不受支持") from exc
        if decision.kind in {DATA_URL, PUBLIC_URL}:
            prepared.append(decision.value)
        elif decision.kind == RELAY_REQUIRED:
            raise BackendUnavailable("Pollinations 参考图需要配置 relay")
        else:
            raise BackendUnavailable("Pollinations 参考图交付方式不受支持")
    return prepared


def _pollinations_json_result(response: Any, *, operation: str) -> dict[str, Any]:
    data = require_mapping(
        safe_json_response(response, provider="Pollinations", operation=operation),
        provider="Pollinations",
        operation=operation,
    )
    items = data.get("data")
    item = items[0] if isinstance(items, list) and items else None
    if isinstance(item, dict):
        url = item.get("url")
        encoded = item.get("b64_json")
        valid_url = isinstance(url, str) and is_http_url(url)
        valid_encoded = isinstance(encoded, str) and bool(encoded.strip())
        if (url is None or valid_url) and (encoded is None or valid_encoded) and (valid_url or valid_encoded):
            return data
    raise ProviderProtocolError("Pollinations 未返回有效的图片数据")


class PollinationsProvider:
    name = "pollinations"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        runtime = resolve_provider_runtime_config(self.name)
        if not runtime.api_key:
            raise BackendUnavailable("Pollinations authentication requires POLLINATIONS_API_KEY", error_category="auth")
        validate_image_operation_request(req, [target])
        width, height = parse_size(req.size)
        references = _pollinations_edit_references(req)
        wants_edit = req.operation == "edit" or bool(references)

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

        async with provider_client(timeout=C.HTTP_TIMEOUT, provider_id=self.name) as client:
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

    def health(self) -> str:
        return "configured_key" if resolve_provider_runtime_config(self.name).api_key else "not_configured"
