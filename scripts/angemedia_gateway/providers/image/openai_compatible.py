"""OpenAI Image API adapter."""
from __future__ import annotations

import asyncio
import base64
import binascii
from typing import Any

from ...image_markup import validate_image_markup
from ...reference_images import (
    collect_image_reference_values,
    is_safe_image_data_url,
    materialize_gateway_image_reference,
    validate_gateway_image_reference,
)
from ...schemas import ImageRequest
from ...media_response import decode_image_b64, decode_svg_b64
from ..base import RouteTarget
from ..catalog.validation import validate_image_operation_request
from ..errors import BackendUnavailable, ProviderProtocolError
from ..http import provider_client, request_with_provider_errors, safe_json_response
from ..parsers import require_mapping
from ..runtime_config import resolve_provider_runtime_config
from ..reference_delivery import MULTIPART, ReferenceDeliveryCapabilities, decide_reference_delivery
from .gif_output import inspect_gif_output


_IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_GPT_IMAGE_26_MODELS = frozenset({"gpt-image-2.6-sunburst", "gpt-image-2.6-flare"})
_OUTSIDE_IMAGE_26_CONTRACT = frozenset({
    "svg", "stream", "partial_images",
    "input_fidelity", "extra_body",
})


def openai_image_file_part(value: str, index: int, *, prefix: str = "image") -> tuple[str, bytes, str]:
    try:
        decision = decide_reference_delivery(
            value,
            ReferenceDeliveryCapabilities.from_methods((MULTIPART,)),
        )
    except ValueError as exc:
        raise BackendUnavailable("OpenAI image references must be gateway assets or safe image data URLs") from exc
    if decision.kind != MULTIPART:
        raise BackendUnavailable("OpenAI image reference delivery must use multipart")
    text = decision.value
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
        or req.markup
        or collect_image_reference_values(req)
    )


class OpenAICompatibleImageProvider:
    """Built-in OpenAI Image API provider.

    Custom OpenAI-compatible providers intentionally use a separate conservative
    adapter so OpenAI-specific edit/multipart behavior does not leak into them.
    """

    name = "openai_image"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        native_contract = target.model in _GPT_IMAGE_26_MODELS
        if (native_contract or req.complexity is not None or req.output_format in {"gif", "svg"}
            or req.frame_count is not None or req.frame_duration_ms is not None or req.markup is not None):
            validate_image_operation_request(req, [target])
        if native_contract and _OUTSIDE_IMAGE_26_CONTRACT.intersection(req.model_extra or {}):
            raise BackendUnavailable("当前 GPT Image 2.6 请求包含尚未接入的字段")
        runtime = resolve_provider_runtime_config(self.name)
        if not runtime.api_key:
            raise BackendUnavailable("OPENAI_IMAGE_API_KEY / OPENAI_API_KEY is not configured")

        if _is_edit_request(req):
            data = await self._edit(req, target, runtime.api_key, runtime.base_url)
        else:
            data = await self._generate(req, target, runtime.api_key, runtime.base_url)
        if req.output_format in {"gif", "svg"}:
            try:
                return await asyncio.to_thread(self._validated_response, data, req, native_contract=native_contract)
            except BackendUnavailable as exc:
                if req.output_format == "svg":
                    # 已收到生成响应，文件拒绝后停止转投，避免再次计费。
                    exc.request_committed = True
                raise
        return self._validated_response(data, req, native_contract=native_contract)

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
        payload.update(self._request_options(req))

        async with provider_client(provider_id=self.name) as client:
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

        if req.markup:
            files = await asyncio.to_thread(self._markup_edit_files, references, req.markup)
        else:
            files = [("image[]", openai_image_file_part(reference, index))
                     for index, reference in enumerate(references, start=1)]
            if req.mask:
                files.append(("mask", openai_image_file_part(req.mask, 1, prefix="mask")))
        if target.model in _GPT_IMAGE_26_MODELS:
            for role, (_, _, mime) in files:
                if mime == "image/gif" or (role == "mask" and mime != "image/png"):
                    raise BackendUnavailable("GPT Image 2.6 的参考图须为 PNG、JPEG 或 WebP，遮罩须为 PNG")

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
        form.update({key: str(value) for key, value in self._request_options(req).items()})

        async with provider_client(provider_id=self.name) as client:
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
    def _markup_edit_files(references: list[str], markup: str) -> list[tuple[str, tuple[str, bytes, str]]]:
        first = openai_image_file_part(references[0], 1)
        overlay = openai_image_file_part(validate_gateway_image_reference(markup), 1, prefix="markup")
        # 校验实际发送的字节，并保留原图和标注各自的内容及顺序。
        validate_image_markup(first[1], overlay[1])
        files = [("image[]", first)]
        files.extend(("image[]", openai_image_file_part(reference, index))
                     for index, reference in enumerate(references[1:], start=2))
        files.append(("markup", overlay))
        return files

    @staticmethod
    def _request_options(req: ImageRequest) -> dict[str, Any]:
        """只发送显式设置，保留上游默认复杂度和合法的零压缩值。"""
        fields = ("output_format", "output_compression", "background", "complexity", "frame_count", "frame_duration_ms")
        return {name: getattr(req, name) for name in fields if getattr(req, name) is not None}

    @staticmethod
    def _validated_response(data: dict[str, Any], req: ImageRequest, *, native_contract: bool = False) -> dict[str, Any]:
        items = data.get("data") or [{}]
        if native_contract and (not isinstance(items, list) or len(items) != 1):
            raise ProviderProtocolError("GPT Image 2.6 应返回单份作品")
        item = items[0] if isinstance(items, list) and items else {}
        if not isinstance(item, dict) or (not item.get("url") and not item.get("b64_json")):
            raise BackendUnavailable("OpenAI image provider did not return image data")
        expected = req.output_format or ("png" if native_contract else None)
        declared = data.get("output_format")
        accepted_formats = {"png", "jpeg", "webp"}
        if expected in {"gif", "svg"}:
            accepted_formats.add(expected)
        if declared is not None and (
            not isinstance(declared, str) or declared not in accepted_formats
            or expected is not None and declared != expected
        ):
            raise ProviderProtocolError("OpenAI image output format did not match the requested format")
        controlled = native_contract or bool(OpenAICompatibleImageProvider._request_options(req))
        if item.get("b64_json") and (controlled or declared is not None):
            try:
                if expected == "svg":
                    content, actual = decode_svg_b64(item["b64_json"]), "svg"
                else:
                    content, extension = decode_image_b64(item["b64_json"])
                    actual = {".png": "png", ".jpg": "jpeg", ".webp": "webp", ".gif": "gif"}.get(extension)
            except RuntimeError as exc:
                message = "OpenAI SVG output did not pass static validation" if expected == "svg" else "OpenAI image provider returned invalid encoded image data"
                raise ProviderProtocolError(message) from exc
            if actual == "gif" and expected != "gif":
                raise ProviderProtocolError("OpenAI image returned GIF for a static output request")
            if actual is None or any(value is not None and value != actual for value in (expected, declared)):
                raise ProviderProtocolError("OpenAI image output bytes did not match the output format")
            if actual == "gif":
                data.update(inspect_gif_output(content, req))
            data["output_format"] = actual
            if controlled:
                # 已核对的编码作品优先，不能再用并存URL替换成未经格式核对的内容。
                item.pop("url", None)
        elif not item.get("b64_json") and controlled:
            # 新输出选项要求可校验的编码数据；旧兼容 URL 响应仍沿原路径处理。
            raise ProviderProtocolError("OpenAI image output controls require encoded image data")
        return data

    def health(self) -> str:
        return "configured" if resolve_provider_runtime_config(self.name).api_key else "not_configured"
