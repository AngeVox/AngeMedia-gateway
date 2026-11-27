"""Safe queued image admission backed by the transactional outbox."""
from __future__ import annotations

import base64
from collections.abc import Callable
import math
from typing import Any

from ..image_markup import ImageMarkupError, validate_image_markup
from ..reference_images import (
    UnsafeImageReference,
    collect_image_reference_values,
    materialize_gateway_image_reference,
)
from ..request_hash_builders import (
    IMAGE_EXTRA_ALLOWLIST,
    build_image_request_hash_payload,
    build_legacy_image_request_hash_payload_v1,
)
from ..routing import resolve_chain
from ..schemas import ImageRequest
from .. import config as C
from ..providers.catalog.loader import load_provider_catalog
from ..providers.catalog.validation import image_operation_for_request
from ..job_submission import JobSubmissionKey, PreparedJobAdmission
from ..repositories.settings import get_custom_provider
from .image_execution import ImageExecutionPlan, build_image_execution_plan
from .job_admission import AdmissionResult, JobAdmissionService
from .queue_smoke import queue_smoke_enabled, smoke_image_plan
from .request_dedupe import IMAGE_REQUEST_HASH_VERSION, REQUEST_HASH_VERSION, request_hash_fields

IMAGE_JOB_PAYLOAD_SCHEMA_VERSION = 1
_REQUEST_FIELDS = set(ImageRequest.model_fields)
_ALLOWED_FIELDS = _REQUEST_FIELDS | set(IMAGE_EXTRA_ALLOWLIST)


def _omit_implicit_unused_size(req: ImageRequest, payload: dict[str, Any], plan: ImageExecutionPlan) -> None:
    """引用归属与路由校验完成后，只移除操作不会使用的隐式尺寸。"""
    # 只省略所有已知目标都不使用、且用户未填写的默认尺寸；实际生成默认值仍固定保存。
    if 'size' not in req.model_fields_set and plan.mode == 'builtin':
        models = [model for model in load_provider_catalog().models
                  if (model.provider, model.provider_model) in plan.routes]
        operations = [model.operations.get(image_operation_for_request(req, model)) for model in models]
        if len(models) == len(plan.routes) and operations and all(
            operation is not None and operation.supported and 'size' not in operation.params
            for operation in operations
        ):
            payload.pop('size', None)


def _canonical_request(req: ImageRequest, *, preflight_markup: bool = False) -> dict[str, Any]:
    payload = req.model_dump(exclude_none=True)
    unknown = set(payload) - _ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"unsupported queued image fields: {', '.join(sorted(unknown))}")
    if req.response_format != "url":
        raise ValueError("queued image jobs require response_format=url")
    references = collect_image_reference_values(req)
    if req.markup and not references:
        raise ImageMarkupError("source_required")
    for index, reference in enumerate(references):
        # 持久任务只保存网关路径；标注按实际发送顺序绑定第一张图。
        if index == 0 and req.markup and preflight_markup:
            validate_image_markup(_markup_reference_bytes(reference, "source"), _markup_reference_bytes(req.markup, "overlay"))
        else:
            materialize_gateway_image_reference(reference)
    if req.markup and not preflight_markup:
        materialize_gateway_image_reference(req.markup)
    if req.mask:
        materialize_gateway_image_reference(req.mask)
    return payload


def _markup_reference_bytes(reference: str, role: str) -> bytes:
    try:
        data_url = materialize_gateway_image_reference(reference)
    except UnsafeImageReference as exc:
        raise ImageMarkupError(f"{role}_unreadable") from exc
    return base64.b64decode(data_url.partition(",")[2], validate=True)


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

    def submit(self, req: ImageRequest, *, submission_key: JobSubmissionKey | None = None) -> AdmissionResult:
        arguments = self.prepare(req).arguments()
        if submission_key is not None:
            arguments['submission_key'] = submission_key
        return self.admission_service.admit(**arguments)

    def prepare(self, req: ImageRequest) -> PreparedJobAdmission:
        """复用正式路由与校验，只准备快照；不创建任务或投递记录。"""
        request_payload = _canonical_request(req, preflight_markup=True)
        if queue_smoke_enabled():
            plan = smoke_image_plan(req)
        else:
            plan = build_image_execution_plan(
                req,
                resolve_chain_func=self.resolve_chain_func,
                get_custom_provider_func=self.get_custom_provider_func,
            )
        _omit_implicit_unused_size(req, request_payload, plan)
        resolved_chain = [
            {"provider": provider, "model": model} for provider, model in plan.routes
        ]
        hash_result = build_image_request_hash_payload(
            req,
            provider_mode=plan.mode,
            resolved_chain=resolved_chain,
            custom_provider_id=plan.custom_provider_id,
            custom_default_model=plan.custom_default_model,
        )
        request_hash, request_hash_version = request_hash_fields(
            hash_result,
            version=IMAGE_REQUEST_HASH_VERSION,
        )
        legacy_hash = request_hash_fields(
            build_legacy_image_request_hash_payload_v1(
                req,
                provider_mode=plan.mode,
                resolved_chain=resolved_chain,
                custom_provider_id=plan.custom_provider_id,
                custom_default_model=plan.custom_default_model,
            ),
            version=REQUEST_HASH_VERSION,
        )
        payload = {
            "schema_version": IMAGE_JOB_PAYLOAD_SCHEMA_VERSION,
            "request": request_payload,
            "route": plan.to_dict(),
        }
        max_attempts = 3
        if plan.mode == 'builtin' and any(provider == 'modelscope' for provider, _ in plan.routes):
            interval = max(0.1, float(C.POLL_INTERVAL))
            maximum = max(interval, float(C.MAX_POLL_TIME))
            payload['async_image'] = {'version': 1, 'interval': interval, 'maximum': maximum}
            max_attempts = min(1000, max(3, math.ceil(maximum / interval) + 5))
        return PreparedJobAdmission.from_arguments(
            kind="image",
            stage="image_generate",
            request_hash=request_hash,
            request_hash_version=request_hash_version,
            payload=payload,
            provider=plan.provider,
            model=plan.model,
            prompt=req.prompt,
            payload_schema_version=IMAGE_JOB_PAYLOAD_SCHEMA_VERSION,
            max_attempts=max_attempts,
            dedupe_hashes=(legacy_hash,),
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
