"""BytePlus ModelArk Seedream image adapter."""
from __future__ import annotations

from typing import Any

from ...reference_images import collect_image_reference_values
from ...schemas import ImageRequest
from ..base import RouteTarget
from ..errors import BackendUnavailable, ProviderProtocolError
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping
from ..runtime_config import resolve_provider_runtime_config
from ..reference_delivery import PUBLIC_URL, RELAY_REQUIRED, prepare_builtin_reference
from ..reference_relay import prepare_reference_with_configured_relay


SEEDREAM_5_MODELS = {
    "dola-seedream-5-0-pro-260628",
    "seedream-5-0-260128",
    "seedream-5-0-lite-260128",
}


def _seedream_public_references(req: ImageRequest, model: str) -> list[str]:
    references: list[str] = []
    for value in collect_image_reference_values(req):
        try:
            decision = prepare_builtin_reference("bytedance", value, model=model)
        except ValueError as exc:
            raise BackendUnavailable("BytePlus Seedream 参考图格式不受支持") from exc
        if decision.kind == PUBLIC_URL:
            references.append(decision.value)
        elif decision.kind == RELAY_REQUIRED:
            raise BackendUnavailable("BytePlus Seedream 本地参考图需要配置 reference relay")
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

    references = _seedream_public_references(req, target.model)
    if references:
        payload["image"] = references[0] if len(references) == 1 else references
    return payload


class ByteDanceImageProvider:
    """Compatibility adapter name retained for the existing `bytedance` provider id."""

    name = "bytedance"

    async def _prepare_runtime_request(self, req: ImageRequest, target: RouteTarget) -> ImageRequest:
        if target.model not in SEEDREAM_5_MODELS:
            return req
        updates: dict[str, Any] = {}
        for field in ("image", "reference_images", "images"):
            current = getattr(req, field, None)
            if current is None:
                continue
            values = current if isinstance(current, list) else [current]
            prepared: list[str] = []
            for value in values:
                if not isinstance(value, str) or not value.strip():
                    continue
                decision = await prepare_reference_with_configured_relay(
                    self.name, value, model=target.model
                )
                if decision.kind != PUBLIC_URL:
                    raise BackendUnavailable("BytePlus Seedream reference could not be published as a public URL")
                prepared.append(decision.value)
            if isinstance(current, list):
                updates[field] = prepared
            elif prepared:
                updates[field] = prepared[0]
        return req.model_copy(update=updates) if updates else req

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        runtime = resolve_provider_runtime_config(self.name)
        if not runtime.api_key:
            raise BackendUnavailable("BYTEDANCE_API_KEY is not configured")
        req = await self._prepare_runtime_request(req, target)

        async with provider_client(provider_id=self.name) as client:
            response = await request_with_provider_errors(
                client,
                "POST",
                f"{runtime.base_url}/images/generations",
                provider="BytePlus Seedream",
                operation="generate",
                headers={
                    "Authorization": f"Bearer {runtime.api_key}",
                    "Content-Type": "application/json",
                },
                json=build_bytedance_image_payload(req, target),
            )

        data = require_mapping(
            safe_json_response(response, provider="BytePlus Seedream", operation="generate"),
            provider="BytePlus Seedream",
            operation="generate",
        )
        items = data.get("data")
        item = items[0] if isinstance(items, list) and items else None
        if not isinstance(item, dict):
            raise ProviderProtocolError("BytePlus Seedream generate failed: missing image data")
        if isinstance(item.get("b64_json"), str) and item["b64_json"]:
            return {"data": [{"b64_json": item["b64_json"]}]}
        if isinstance(item.get("url"), str) and item["url"]:
            return {"data": [{"url": item["url"]}]}
        raise ProviderProtocolError("BytePlus Seedream generate failed: missing image output")

    def health(self) -> str:
        return "configured" if resolve_provider_runtime_config(self.name).api_key else "not_configured"
