"""Custom OpenAI-compatible image provider runtime."""
from __future__ import annotations

from typing import Any

from ..reference_images import collect_image_reference_values
from ..schemas import ImageRequest
from ..security import ensure_public_http_url
from .base import BackendUnavailable
from .custom_capabilities import validate_custom_image_request
from .http import provider_client, request_with_provider_errors, safe_json_response
from .image.openai_compatible import openai_image_file_part
from .parsers import require_mapping


def _validate_custom_response(data: dict[str, Any]) -> dict[str, Any]:
    item_list = data.get("data") or [{}]
    item = item_list[0] if isinstance(item_list, list) and item_list else {}
    if not isinstance(item, dict) or (not item.get("url") and not item.get("b64_json")):
        raise BackendUnavailable("自定义渠道没有返回图片数据")
    return data


async def _custom_generate(
    req: ImageRequest,
    *,
    base_url: str,
    api_key: str,
    model: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": req.prompt,
        "n": 1,
        "size": req.size,
        "response_format": req.response_format,
    }
    if req.quality:
        payload["quality"] = req.quality
    if req.user:
        payload["user"] = req.user
    if req.negative_prompt:
        payload["negative_prompt"] = req.negative_prompt
    if req.seed is not None:
        payload["seed"] = req.seed

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with provider_client() as client:
        response = await request_with_provider_errors(
            client,
            "POST",
            f"{base_url}/images/generations",
            provider="custom image provider",
            operation="generate",
            headers=headers,
            json=payload,
        )
    return require_mapping(
        safe_json_response(response, provider="custom image provider", operation="generate"),
        provider="custom image provider",
        operation="generate",
    )


async def _custom_edit(
    req: ImageRequest,
    *,
    base_url: str,
    api_key: str,
    model: str,
) -> dict[str, Any]:
    references = collect_image_reference_values(req)
    files: list[tuple[str, tuple[str, bytes, str]]] = [
        ("image[]", openai_image_file_part(value, index))
        for index, value in enumerate(references, start=1)
    ]
    if req.mask:
        files.append(("mask", openai_image_file_part(req.mask, 1, prefix="mask")))

    form: dict[str, str] = {
        "model": model,
        "prompt": req.prompt,
        "n": "1",
        "size": req.size,
    }
    if req.quality:
        form["quality"] = req.quality
    if req.user:
        form["user"] = req.user

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with provider_client() as client:
        response = await request_with_provider_errors(
            client,
            "POST",
            f"{base_url}/images/edits",
            provider="custom image provider",
            operation="edit",
            headers=headers,
            data=form,
            files=files,
        )
    return require_mapping(
        safe_json_response(response, provider="custom image provider", operation="edit"),
        provider="custom image provider",
        operation="edit",
    )


async def generate_custom_openai_image(req: ImageRequest, provider: dict[str, Any]) -> dict[str, Any]:
    """Call an explicitly configured OpenAI-compatible image provider.

    Text-to-image is always the conservative default. Multipart image edits are
    enabled only when the administrator explicitly declares that capability.
    """
    if not provider.get("enabled"):
        raise BackendUnavailable("自定义渠道已停用")

    try:
        base_url = ensure_public_http_url(str(provider.get("base_url") or "").rstrip("/"))
    except ValueError as exc:
        raise BackendUnavailable(str(exc)) from exc
    api_key = str(provider.get("api_key") or "")
    model = str(req.provider_model or provider.get("default_model") or "")
    if not base_url or not model:
        raise BackendUnavailable("自定义渠道缺少 base_url 或 default_model")

    try:
        capabilities = validate_custom_image_request(req, provider)
    except ValueError as exc:
        raise BackendUnavailable(str(exc)) from exc
    wants_edit = req.operation == "edit" or bool(collect_image_reference_values(req)) or bool(req.mask)
    if wants_edit and capabilities["image_edit"]:
        data = await _custom_edit(req, base_url=base_url, api_key=api_key, model=model)
    else:
        data = await _custom_generate(req, base_url=base_url, api_key=api_key, model=model)
    return _validate_custom_response(data)
