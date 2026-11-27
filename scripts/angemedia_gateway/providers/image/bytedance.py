"""BytePlus ModelArk Seedream image adapter."""
from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

import httpx

from ...layered_images import LAYER_MODEL, LayeredImageRequest, layer_reference, parse_layer_result
from ...media_execution_scope import require_execution_target
from ...media_response import decode_image_b64
from ...reference_images import collect_image_reference_values
from ...schemas import ImageRequest
from ..base import RouteTarget
from ..catalog.validation import validate_image_operation_request
from ..errors import BackendUnavailable, ProviderProtocolError
from ..http import normalize_httpx_error, provider_client, raise_for_provider_status, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping
from ..runtime_config import resolve_provider_runtime_config
from ..reference_delivery import DATA_URL, PUBLIC_URL, prepare_builtin_reference


SEEDREAM_55_MODEL = LAYER_MODEL
_LAYER_RESPONSE_MAX_BYTES = 4 * 1024 * 1024

SEEDREAM_5_MODELS = {
    SEEDREAM_55_MODEL,
    "dola-seedream-5-0-pro-260628",
    "dola-seedream-5-0-flash-260915",
    "seedream-5-0-260128",
    "seedream-5-0-lite-260128",
}


def _seedream_references(req: ImageRequest, model: str) -> list[str]:
    """按共有交付策略直送受控图片或保留公网URL，不另行上传中转。"""
    references: list[str] = []
    for value in collect_image_reference_values(req):
        try:
            decision = prepare_builtin_reference("bytedance", value, model=model)
        except ValueError as exc:
            raise BackendUnavailable("BytePlus Seedream 参考图格式不受支持") from exc
        if decision.kind in {DATA_URL, PUBLIC_URL}:
            references.append(decision.value)
        else:
            raise BackendUnavailable("BytePlus Seedream 参考图交付方式不受支持")
    return references


def build_bytedance_image_payload(req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
    """Build a model-specific BytePlus ModelArk image request."""
    if target.model not in SEEDREAM_5_MODELS:
        payload: dict[str, Any] = {
            "model": target.model,
            "prompt": req.prompt,
            "size": req.size,
            "response_format": "b64_json",
        }
        if req.seed is not None:
            payload["seed"] = req.seed
        return payload

    payload = {
        "model": target.model,
        "prompt": req.prompt,
        "size": req.size,
        "response_format": req.response_format,
    }
    if req.output_format:
        payload["output_format"] = req.output_format
    if req.watermark is not None:
        payload["watermark"] = req.watermark
    if target.model == SEEDREAM_55_MODEL:
        validate_image_operation_request(req, [target])
        if req.background is not None:
            payload["background"] = req.background

    references = _seedream_references(req, target.model)
    if references:
        payload["image"] = references[0] if len(references) == 1 else references
    return payload


def _validate_seedream_55_output(req: ImageRequest, item: dict[str, Any]) -> None:
    """核对返回的格式声明；内联媒体还可以直接检查文件头。"""
    expected = req.output_format or "png"
    declared = item.get("output_format")
    if declared is not None and declared != expected:
        raise ProviderProtocolError("BytePlus Seedream output format did not match the request")
    if item.get("b64_json"):
        try:
            _, extension = decode_image_b64(item["b64_json"])
        except RuntimeError as exc:
            raise ProviderProtocolError("BytePlus Seedream returned invalid encoded image data") from exc
        if extension != (".jpg" if expected == "jpeg" else ".png"):
            raise ProviderProtocolError("BytePlus Seedream output bytes did not match the output format")


class ByteDanceImageProvider:
    """Compatibility adapter name retained for the existing `bytedance` provider id."""

    name = "bytedance"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        payload = build_bytedance_image_payload(req, target)
        data = await self._request(payload)
        items = data.get("data")
        # 当前只承诺单图交付；不能丢掉额外结果后把半份输出当作完成。
        if not isinstance(items, list) or len(items) != 1:
            raise ProviderProtocolError("BytePlus Seedream generate failed: unexpected image result count")
        item = items[0]
        if not isinstance(item, dict):
            raise ProviderProtocolError("BytePlus Seedream generate failed: missing image data")
        # 分层底图的 z_index 是 0，仅判断数组长度或真值会把半份结果当作单图。
        if any(item.get(field) is not None for field in ("z_index", "bounding_box", "text_recognition")):
            raise ProviderProtocolError("BytePlus Seedream generate failed: unexpected layer output")
        if item.get("error") is not None:
            raise ProviderProtocolError("BytePlus Seedream generate failed: image result reports an error")
        if target.model == SEEDREAM_55_MODEL:
            _validate_seedream_55_output(req, item)
        if isinstance(item.get("b64_json"), str) and item["b64_json"]:
            return {"data": [{"b64_json": item["b64_json"]}]}
        if isinstance(item.get("url"), str) and item["url"]:
            return {"data": [{"url": item["url"]}]}
        raise ProviderProtocolError("BytePlus Seedream generate failed: missing image output")

    async def decompose(self, request: LayeredImageRequest) -> tuple[list[dict], datetime]:
        image = layer_reference(request)
        require_execution_target(self.name, LAYER_MODEL)
        data = await self._request({
            "model": LAYER_MODEL, "prompt": request.prompt, "image": image,
            "layer_decomposition": True,
            "layer_decomposition_options": {"max_images": request.max_images, "text_recognition": request.text_recognition},
            "size": request.size, "output_format": request.output_format, "response_format": "url",
            "watermark": request.watermark,
        }, layered=True)
        return parse_layer_result(data, request, datetime.now(timezone.utc))

    async def _request(self, payload: dict[str, Any], *, layered: bool = False) -> dict[str, Any]:
        runtime = resolve_provider_runtime_config(self.name)
        if not runtime.api_key:
            raise BackendUnavailable("BYTEDANCE_API_KEY is not configured")

        url = f"{runtime.base_url}/images/generations"
        headers = {"Authorization": f"Bearer {runtime.api_key}", "Content-Type": "application/json"}
        async with provider_client(provider_id=self.name) as client:
            if layered:
                response = await self._read_layer_response(client, url, headers, payload)
            else:
                response = await request_with_provider_errors(
                    client, "POST", url, provider="BytePlus Seedream", operation="generate",
                    headers=headers, json=payload,
                )

        return require_mapping(
            safe_json_response(response, provider="BytePlus Seedream", operation="generate"),
            provider="BytePlus Seedream",
            operation="generate",
        )

    @staticmethod
    async def _read_layer_response(client, url, headers, payload):
        """分层收据只请求 JSON 链接，限制解码前的响应体积。"""
        content = bytearray()
        try:
            async with client.stream("POST", url, headers={**headers, "Accept-Encoding": "identity"}, json=payload) as response:
                raise_for_provider_status(response, provider="BytePlus Seedream", operation="decompose")
                async for chunk in response.aiter_bytes(65536):
                    if len(content) + len(chunk) > _LAYER_RESPONSE_MAX_BYTES:
                        raise ProviderProtocolError("BytePlus Seedream 分层响应过大")
                    content.extend(chunk)
        except httpx.HTTPError as exc:
            raise normalize_httpx_error(exc, provider="BytePlus Seedream", operation="decompose") from None
        return httpx.Response(200, content=bytes(content))

    def health(self) -> str:
        return "configured" if resolve_provider_runtime_config(self.name).api_key else "not_configured"
