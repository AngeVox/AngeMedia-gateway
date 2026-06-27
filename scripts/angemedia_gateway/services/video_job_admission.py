"""Safe queued video admission backed by the transactional outbox."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..reference_images import materialize_gateway_image_reference
from ..repositories.settings import builtin_provider_enabled
from ..request_hash_builders import build_video_request_hash_payload
from ..schemas import VideoRequest
from .job_admission import AdmissionResult, JobAdmissionService
from .queue_smoke import queue_smoke_enabled
from .request_dedupe import request_hash_fields
from .video_execution import VideoProviderDisabled
from .video_polling import VideoPipelinePolicy

VIDEO_JOB_PAYLOAD_SCHEMA_VERSION = 1


def _canonical_request(req: VideoRequest) -> dict[str, Any]:
    if req.wait_for_completion:
        raise ValueError("queued video jobs cannot wait for completion")
    if req.extra_body:
        raise ValueError("queued video jobs do not accept extra_body")
    references = ([req.image] if req.image else []) + list(req.images or [])
    for reference in references:
        materialize_gateway_image_reference(reference)
    payload = req.model_dump(exclude_none=True)
    payload.pop("extra_body", None)
    payload["wait_for_completion"] = False
    return payload


class VideoJobAdmissionService:
    def __init__(
        self,
        *,
        admission_service: JobAdmissionService | None = None,
        provider_enabled_func: Callable[[str], bool] = builtin_provider_enabled,
        policy: VideoPipelinePolicy | None = None,
    ) -> None:
        self.admission_service = admission_service or JobAdmissionService()
        self.provider_enabled_func = provider_enabled_func
        self.policy = policy or VideoPipelinePolicy.from_config()

    def submit(self, req: VideoRequest) -> AdmissionResult:
        if not queue_smoke_enabled() and not self.provider_enabled_func("agnes_video"):
            raise VideoProviderDisabled("Agnes video provider is disabled")
        request_payload = _canonical_request(req)
        request_hash, request_hash_version = request_hash_fields(
            build_video_request_hash_payload(req, provider="agnes_video")
        )
        payload = {
            "schema_version": VIDEO_JOB_PAYLOAD_SCHEMA_VERSION,
            "pipeline": "video",
            "provider": "agnes_video",
            "input_mode": req.mode or ("image" if req.image or req.images else "text"),
            "request": request_payload,
        }
        return self.admission_service.admit(
            kind="video",
            stage="video_submit",
            request_hash=request_hash,
            request_hash_version=request_hash_version,
            payload=payload,
            provider="agnes_video",
            model=req.model,
            prompt=req.prompt,
            payload_schema_version=VIDEO_JOB_PAYLOAD_SCHEMA_VERSION,
            max_attempts=self.policy.max_attempts,
        )


def parse_video_job_payload(value: Any) -> tuple[VideoRequest, str]:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != VIDEO_JOB_PAYLOAD_SCHEMA_VERSION
        or value.get("pipeline") != "video"
        or value.get("provider") != "agnes_video"
        or not isinstance(value.get("request"), dict)
    ):
        raise ValueError("invalid queued video payload")
    req = VideoRequest(**value["request"])
    _canonical_request(req)
    input_mode = str(value.get("input_mode") or "text")[:64]
    return req, input_mode


def is_worker_managed_video_payload(raw: Any) -> bool:
    if not raw:
        return False
    try:
        import json

        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return False
    return isinstance(value, dict) and value.get("pipeline") == "video"
