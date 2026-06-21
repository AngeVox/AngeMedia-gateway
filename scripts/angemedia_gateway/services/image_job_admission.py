"""Safe queued image admission backed by the transactional outbox."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..reference_images import materialize_gateway_image_reference
from ..request_hash_builders import IMAGE_EXTRA_ALLOWLIST, build_image_request_hash_payload
from ..routing import resolve_chain
from ..schemas import ImageRequest
from ..repositories.settings import get_custom_provider
from .image_execution import ImageExecutionPlan, build_image_execution_plan
from .job_admission import AdmissionResult, JobAdmissionService
from .request_dedupe import request_hash_fields

IMAGE_JOB_PAYLOAD_SCHEMA_VERSION = 1
_REQUEST_FIELDS = set(ImageRequest.model_fields)
_ALLOWED_FIELDS = _REQUEST_FIELDS | set(IMAGE_EXTRA_ALLOWLIST)


def _canonical_request(req: ImageRequest) -> dict[str, Any]:
    payload = req.model_dump(exclude_none=True)
    unknown = set(payload) - _ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"unsupported queued image fields: {', '.join(sorted(unknown))}")
    if req.response_format != "url":
        raise ValueError("queued image jobs require response_format=url")
    if req.image:
        # Validate ownership, MIME, size, existence and traversal before persisting only the identity.
        materialize_gateway_image_reference(req.image)
    return payload


class ImageJobAdmissionService:
    def __init__(
        self,
        *,
        admission_service: JobAdmissionService | None = None,
        resolve_chain_func: Callable[[str | None], list[Any]] = resolve_chain,
        get_custom_provider_func: Callable[..., dict[str, Any] | None] = get_custom_provider,
    ) -> None:
        self.admission_service = admission_service or JobAdmissionService()
        self.resolve_chain_func = resolve_chain_func
        self.get_custom_provider_func = get_custom_provider_func

    def submit(self, req: ImageRequest) -> AdmissionResult:
        request_payload = _canonical_request(req)
        plan = build_image_execution_plan(
            req,
            resolve_chain_func=self.resolve_chain_func,
            get_custom_provider_func=self.get_custom_provider_func,
        )
        hash_result = build_image_request_hash_payload(
            req,
            provider_mode=plan.mode,
            resolved_chain=[
                {"provider": provider, "model": model} for provider, model in plan.routes
            ],
            custom_provider_id=plan.custom_provider_id,
            custom_default_model=plan.custom_default_model,
        )
        request_hash, request_hash_version = request_hash_fields(hash_result)
        payload = {
            "schema_version": IMAGE_JOB_PAYLOAD_SCHEMA_VERSION,
            "request": request_payload,
            "route": plan.to_dict(),
        }
        return self.admission_service.admit(
            kind="image",
            stage="image_generate",
            request_hash=request_hash,
            request_hash_version=request_hash_version,
            payload=payload,
            provider=plan.provider,
            model=plan.model,
            prompt=req.prompt,
            payload_schema_version=IMAGE_JOB_PAYLOAD_SCHEMA_VERSION,
        )


def parse_image_job_payload(value: Any) -> tuple[ImageRequest, ImageExecutionPlan]:
    if not isinstance(value, dict) or value.get("schema_version") != IMAGE_JOB_PAYLOAD_SCHEMA_VERSION:
        raise ValueError("unsupported image job payload schema")
    request_value = value.get("request")
    if not isinstance(request_value, dict):
        raise ValueError("image job request payload is invalid")
    unknown = set(request_value) - _ALLOWED_FIELDS
    if unknown:
        raise ValueError("image job request payload contains unsupported fields")
    req = ImageRequest(**request_value)
    _canonical_request(req)
    return req, ImageExecutionPlan.from_dict(value.get("route"))
