"""Pluggable relay contract for Providers that require public reference URLs.

No relay backend is enabled by default. The contract intentionally separates
reference-delivery decisions from the mechanism that publishes a short-lived
public URL (for example an operator-controlled object store or relay service).
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
from typing import Protocol

from ..outbound_http import outbound_client
from ..reference_images import (
    is_safe_image_data_url,
    materialize_gateway_image_reference,
)
from ..security import validate_provider_reference_url
from .endpoint_policy import validate_provider_probe_url
from .reference_delivery import RELAY_REQUIRED, ReferenceDeliveryDecision, prepare_builtin_reference


DEFAULT_RELAY_TTL_SECONDS = 15 * 60
MIN_RELAY_TTL_SECONDS = 60
MAX_RELAY_TTL_SECONDS = 60 * 60


class ReferenceRelayUnavailable(RuntimeError):
    """Raised when a Provider needs a relay but none is configured."""


@dataclass(frozen=True, slots=True)
class RelayPublication:
    url: str
    expires_at: str | None = None


class ReferenceRelayBackend(Protocol):
    """Backend capable of publishing a gateway-owned reference temporarily."""

    async def publish_reference(self, reference: str, *, ttl_seconds: int) -> RelayPublication:
        ...

_IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class ExternalHttpReferenceRelay:
    """Publish a gateway-owned reference through an administrator relay service."""

    def __init__(self, upload_url: str, *, token: str | None = None, timeout: float = 20.0) -> None:
        raw_url = str(upload_url or "").strip()
        if not raw_url:
            raise ValueError("Reference relay upload URL is required.")
        try:
            self.upload_url = validate_provider_probe_url(raw_url)
        except ValueError as exc:
            raise ValueError("Reference relay upload URL is invalid.") from exc
        self.token = str(token or "").strip() or None
        self.timeout = float(timeout)

    async def publish_reference(self, reference: str, *, ttl_seconds: int) -> RelayPublication:
        filename, content, mime = _reference_file_part(reference)
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            async with outbound_client(timeout=self.timeout) as client:
                response = await client.post(
                    self.upload_url,
                    headers=headers,
                    data={"ttl_seconds": str(int(ttl_seconds))},
                    files={"reference": (filename, content, mime)},
                )
        except Exception as exc:
            raise ReferenceRelayUnavailable("Reference relay upload failed: network") from exc
        if int(getattr(response, "status_code", 0) or 0) not in {200, 201, 202}:
            raise ReferenceRelayUnavailable("Reference relay upload failed: upstream")
        try:
            payload = response.json()
        except Exception as exc:
            raise ReferenceRelayUnavailable("Reference relay returned an invalid response.") from exc
        if not isinstance(payload, dict):
            raise ReferenceRelayUnavailable("Reference relay returned an invalid response.")
        url = payload.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ReferenceRelayUnavailable("Reference relay response is missing url.")
        expires_at = payload.get("expires_at")
        return RelayPublication(
            url=url.strip(),
            expires_at=str(expires_at).strip() if isinstance(expires_at, str) and expires_at.strip() else None,
        )


def _reference_file_part(reference: str) -> tuple[str, bytes, str]:
    text = str(reference or "").strip()
    if text.startswith(("/uploads/", "/generated/")):
        data_url = materialize_gateway_image_reference(text)
    elif is_safe_image_data_url(text):
        data_url = text
    else:
        raise ReferenceRelayUnavailable("Reference relay accepts only gateway-owned images or safe image data URLs.")

    header, separator, encoded = data_url.partition(",")
    if not separator or not header.lower().endswith(";base64"):
        raise ReferenceRelayUnavailable("Reference relay image could not be materialized.")
    mime = header[5:-7].lower()
    extension = _IMAGE_EXTENSIONS.get(mime)
    if extension is None:
        raise ReferenceRelayUnavailable("Reference relay image format is not supported.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ReferenceRelayUnavailable("Reference relay image could not be decoded.") from exc
    if not content:
        raise ReferenceRelayUnavailable("Reference relay image is empty.")
    return f"reference{extension}", content, mime


async def fulfill_relay_decision(
    decision: ReferenceDeliveryDecision,
    *,
    backend: ReferenceRelayBackend | None,
    ttl_seconds: int = DEFAULT_RELAY_TTL_SECONDS,
) -> ReferenceDeliveryDecision:
    """Fulfil a ``relay_required`` decision through an explicit backend.

    The relay URL is validated as a Provider-fetchable public URL before it is
    returned. The function never falls back to exposing ``PUBLIC_BASE_URL`` or a
    gateway-local protected URL.
    """

    if decision.kind != RELAY_REQUIRED:
        return decision
    if backend is None:
        raise ReferenceRelayUnavailable("Provider requires a configured reference-image relay.")

    ttl = int(ttl_seconds)
    if ttl < MIN_RELAY_TTL_SECONDS or ttl > MAX_RELAY_TTL_SECONDS:
        raise ValueError("Reference relay TTL is out of range.")

    publication = await backend.publish_reference(decision.value, ttl_seconds=ttl)
    try:
        safe_url = validate_provider_reference_url(publication.url)
    except ValueError as exc:
        raise ReferenceRelayUnavailable("Reference relay did not return a usable public URL.") from exc

    return ReferenceDeliveryDecision(
        kind="public_url",
        value=safe_url,
        source="relay",
    )


def configured_reference_relay_backend() -> ReferenceRelayBackend | None:
    """Build the explicitly configured relay backend, or return None when disabled."""
    from ..repositories.reference_relay_config import get_reference_relay_config
    from ..services.reference_relay_config import DISABLED, EXTERNAL_HTTP

    config = get_reference_relay_config()
    mode = str(config.get("mode") or DISABLED).strip().lower()
    if mode == DISABLED:
        return None
    if mode == EXTERNAL_HTTP:
        upload_url = str(config.get("upload_url") or "").strip()
        if not upload_url:
            raise ReferenceRelayUnavailable("Reference relay is not fully configured.")
        return ExternalHttpReferenceRelay(upload_url, token=config.get("token"))
    raise ReferenceRelayUnavailable("Reference relay mode is not supported.")


async def prepare_reference_with_configured_relay(
    provider_id: str,
    value: str,
    *,
    model: str | None = None,
    ttl_seconds: int = DEFAULT_RELAY_TTL_SECONDS,
) -> ReferenceDeliveryDecision:
    """Resolve one reference and fulfil relay delivery only when required."""
    decision = prepare_builtin_reference(provider_id, value, model=model)
    if decision.kind != RELAY_REQUIRED:
        return decision
    return await fulfill_relay_decision(
        decision,
        backend=configured_reference_relay_backend(),
        ttl_seconds=ttl_seconds,
    )
