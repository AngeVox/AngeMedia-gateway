"""Provider reference-image delivery policy.

This module decides how an already accepted image reference can be delivered to
an upstream Provider. It does not upload files, expose local storage, create
public relay URLs, or fetch arbitrary remote URLs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse

from ..reference_images import (
    is_safe_image_data_url,
    materialize_gateway_image_reference,
)
from ..security import validate_provider_reference_url


DATA_URL = "data_url"
BASE64 = "base64"
MULTIPART = "multipart"
PUBLIC_URL = "public_url"
RELAY_REQUIRED = "relay_required"

_SUPPORTED_METHODS = {DATA_URL, BASE64, MULTIPART, PUBLIC_URL}

_BUILTIN_REFERENCE_METHODS: dict[str, tuple[str, ...]] = {
    "openai_image": (MULTIPART,),
    "modelscope": (DATA_URL, PUBLIC_URL),
    "siliconflow": (DATA_URL, PUBLIC_URL),
    "agnes_image": (DATA_URL, PUBLIC_URL),
    "pollinations": (DATA_URL, PUBLIC_URL),
    "bytedance": (PUBLIC_URL,),
    "agnes_video": (BASE64,),
}

_AGNES_VIDEO_PUBLIC_URL_MODELS = {"agnes-video-2.5", "agnes-video-v2.5"}


@dataclass(frozen=True, slots=True)
class ReferenceDeliveryCapabilities:
    methods: tuple[str, ...]

    @classmethod
    def from_methods(cls, methods: Iterable[str]) -> "ReferenceDeliveryCapabilities":
        normalized: list[str] = []
        for value in methods:
            method = str(value or "").strip().lower()
            if method not in _SUPPORTED_METHODS:
                raise ValueError("Unsupported reference delivery method.")
            if method not in normalized:
                normalized.append(method)
        if not normalized:
            raise ValueError("At least one reference delivery method is required.")
        return cls(methods=tuple(normalized))


@dataclass(frozen=True, slots=True)
class ReferenceDeliveryDecision:
    kind: str
    value: str
    source: str


def builtin_reference_delivery_capabilities(
    provider_id: str,
    model: str | None = None,
) -> ReferenceDeliveryCapabilities:
    provider = str(provider_id or "").strip().lower()
    normalized_model = str(model or "").strip().lower()
    if provider == "agnes_video" and normalized_model in _AGNES_VIDEO_PUBLIC_URL_MODELS:
        return ReferenceDeliveryCapabilities.from_methods((PUBLIC_URL,))
    methods = _BUILTIN_REFERENCE_METHODS.get(provider)
    if methods is None:
        raise ValueError("Provider reference delivery profile is not defined.")
    return ReferenceDeliveryCapabilities.from_methods(methods)


def prepare_builtin_reference(
    provider_id: str,
    value: Any,
    *,
    model: str | None = None,
) -> ReferenceDeliveryDecision:
    return decide_reference_delivery(
        value,
        builtin_reference_delivery_capabilities(provider_id, model=model),
    )


def decide_reference_delivery(
    value: Any,
    capabilities: ReferenceDeliveryCapabilities,
) -> ReferenceDeliveryDecision:
    """Choose a safe delivery form without performing network I/O.

    Local gateway assets prefer multipart when supported, then data URLs, then
    bare base64 payloads. If a Provider only accepts public URLs, the decision is
    ``relay_required`` rather than forcing the user to supply a third-party image
    host.
    """

    text = str(value or "").strip()
    if not text:
        raise ValueError("Reference image is empty.")

    if text.startswith(("/uploads/", "/generated/")):
        if MULTIPART in capabilities.methods:
            return ReferenceDeliveryDecision(kind=MULTIPART, value=text, source="gateway_asset")
        if DATA_URL in capabilities.methods or BASE64 in capabilities.methods:
            data_url = materialize_gateway_image_reference(text)
            if DATA_URL in capabilities.methods:
                return ReferenceDeliveryDecision(kind=DATA_URL, value=data_url, source="gateway_asset")
            return ReferenceDeliveryDecision(
                kind=BASE64,
                value=_data_url_base64_payload(data_url),
                source="gateway_asset",
            )
        if PUBLIC_URL in capabilities.methods:
            return ReferenceDeliveryDecision(kind=RELAY_REQUIRED, value=text, source="gateway_asset")
        raise ValueError("Reference image cannot be delivered to this Provider.")

    if is_safe_image_data_url(text):
        if MULTIPART in capabilities.methods:
            return ReferenceDeliveryDecision(kind=MULTIPART, value=text, source="data_url")
        if DATA_URL in capabilities.methods:
            return ReferenceDeliveryDecision(kind=DATA_URL, value=text, source="data_url")
        if BASE64 in capabilities.methods:
            return ReferenceDeliveryDecision(
                kind=BASE64,
                value=_data_url_base64_payload(text),
                source="data_url",
            )
        if PUBLIC_URL in capabilities.methods:
            return ReferenceDeliveryDecision(kind=RELAY_REQUIRED, value=text, source="data_url")
        raise ValueError("Reference image cannot be delivered to this Provider.")

    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"}:
        if PUBLIC_URL not in capabilities.methods:
            raise ValueError("Provider does not accept remote reference URLs.")
        try:
            safe_url = validate_provider_reference_url(text)
        except ValueError as exc:
            raise ValueError("Reference image URL is not allowed.") from exc
        return ReferenceDeliveryDecision(kind=PUBLIC_URL, value=safe_url, source="remote_url")

    raise ValueError("Reference image format is not supported.")


def _data_url_base64_payload(value: str) -> str:
    if not is_safe_image_data_url(value):
        raise ValueError("Reference image data URL is invalid.")
    _, separator, encoded = value.partition(",")
    if not separator or not encoded:
        raise ValueError("Reference image data URL is invalid.")
    return encoded
