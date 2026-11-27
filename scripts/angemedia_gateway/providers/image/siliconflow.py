"""SiliconFlow image adapter."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ...media import openai_image_response
from ...reference_images import collect_image_reference_values
from ...schemas import ImageRequest
from ..base import RouteTarget
from ..errors import BackendUnavailable, ProviderProtocolError
from ..catalog.loader import load_provider_catalog
from ..catalog.validation import validate_image_operation_request
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping
from ..runtime_config import resolve_provider_runtime_config
from ..reference_delivery import DATA_URL, PUBLIC_URL, RELAY_REQUIRED, prepare_builtin_reference


QWEN_IMAGE_EDIT_2509 = "Qwen/Qwen-Image-Edit-2509"
QWEN_IMAGE = "Qwen/Qwen-Image"
KOLORS = "Kwai-Kolors/Kolors"


def _provider_image_reference(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        decision = prepare_builtin_reference("siliconflow", value)
    except ValueError as error:
        raise BackendUnavailable("SiliconFlow 参考图格式不受支持") from error
    if decision.kind in {DATA_URL, PUBLIC_URL}:
        return decision.value
    if decision.kind == RELAY_REQUIRED:
        raise BackendUnavailable("SiliconFlow 参考图需要配置 relay")
    raise BackendUnavailable("SiliconFlow 参考图交付方式不受支持")


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
    # 入队和直接调用适配器共用同一合同，避免未知型号借用已知型号能力。
    catalog = load_provider_catalog()
    validate_image_operation_request(req, [target], catalog=catalog)
    if target.model == QWEN_IMAGE_EDIT_2509:
        return _qwen_edit_payload(req, target)
    if target.model in {QWEN_IMAGE, KOLORS}:
        # 已收录型号只按自身目录映射字段与默认值；参考图仍走原交付策略。
        model = catalog.models_by_id["kolors" if target.model == KOLORS else "siliconflow-qwen-image"]
        references = collect_image_reference_values(req)
        if not references and getattr(req, "input_image", None):
            # 目录允许的Kolors旧字段也要实际交付；其他型号已在同一目录校验中拒绝该字段。
            references = [req.input_image.strip()]
        operation = model.operations["image_to_image" if references else "text_to_image"]
        payload = {"model": target.model}
        for name, spec in operation.params.items():
            value = getattr(req, name, None)
            if value is None:
                value = spec.default
            if spec.provider_field and value is not None:
                payload[spec.provider_field] = value
        if references:
            payload["image"] = _provider_image_reference(references[0])
        return payload

    # 显式未知型号保留原始尺寸，仅传递调用者明确提供的通用字段；不猜默认步数和引导字段。
    payload = {"model": target.model, "prompt": req.prompt, "image_size": req.size}
    for name, field in (("steps", "num_inference_steps"), ("seed", "seed"), ("negative_prompt", "negative_prompt")):
        value = getattr(req, name)
        if value is not None:
            payload[field] = value
    return payload


class SiliconFlowProvider:
    name = "siliconflow"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        runtime = resolve_provider_runtime_config(self.name)
        if not runtime.api_key:
            raise BackendUnavailable("SILICONFLOW_API_KEY is not configured")

        payload = build_siliconflow_image_payload(req, target)
        async with provider_client(provider_id=self.name) as client:
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
                json=payload,
            )

        data = require_mapping(
            safe_json_response(resp, provider="SiliconFlow", operation="generate"),
            provider="SiliconFlow",
            operation="generate",
        )
        images = data.get("images")
        if not isinstance(images, list) or not images:
            raise ProviderProtocolError("SiliconFlow 未返回有效的图片列表")
        item = require_mapping(images[0], provider="SiliconFlow", operation="generate")
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ProviderProtocolError("SiliconFlow 未返回有效的图片地址")
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("invalid image URL")
        except ValueError as exc:
            raise ProviderProtocolError("SiliconFlow 未返回有效的图片地址") from exc
        return openai_image_response(url=url)

    def health(self) -> str:
        return "configured" if resolve_provider_runtime_config(self.name).api_key else "not_configured"
