"""Safe queued video admission backed by the transactional outbox."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from typing import Any

from ..db.connection import db_connect
from ..job_sanitizer import sanitize_error_text, sanitize_job_value
from ..job_submission import JobSubmissionKey, PreparedJobAdmission
from ..pollinations_video import POLLINATIONS_VIDEO_MAX_ATTEMPTS, PollinationsVideoRequest
from ..providers.reference_delivery import RELAY_REQUIRED, prepare_builtin_reference
from ..providers.reference_relay import configured_reference_relay_backend
from ..providers.runtime_config import resolve_live_provider_runtime_config
from ..reference_images import validate_gateway_image_reference
from ..repositories.settings import builtin_provider_enabled
from ..request_hash_builders import build_video_request_hash_payload
from ..schemas import VideoRequest
from ..video_models import is_agnes_video_modern
from .job_admission import AdmissionResult, JobAdmissionService
from .queue_smoke import queue_smoke_enabled
from .request_dedupe import request_hash_fields
from .video_execution import VideoProviderDisabled
from .video_polling import VideoPipelinePolicy
from .video_references import validate_video_reference_sources

QueuedVideoRequest = PollinationsVideoRequest | VideoRequest
VIDEO_JOB_PAYLOAD_SCHEMA_VERSION = 1


def _canonical_request(req: VideoRequest) -> dict[str, Any]:
    if req.wait_for_completion:
        raise ValueError("queued video jobs cannot wait for completion")
    if req.extra_body:
        raise ValueError("queued video jobs do not accept extra_body")
    validate_video_reference_sources(req, materialize=True)
    payload = req.model_dump(exclude_none=True)
    payload.pop("extra_body", None)
    if is_agnes_video_modern(req.model):
        for legacy_field in (
            "height", "width", "num_frames", "frame_rate", "negative_prompt", "num_inference_steps"
        ):
            payload.pop(legacy_field, None)
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

    def submit(self, req: QueuedVideoRequest, *, submission_key: JobSubmissionKey | None = None) -> AdmissionResult:
        arguments = self.prepare(req).arguments()
        if submission_key is not None:
            arguments['submission_key'] = submission_key
        return self.admission_service.admit(**arguments)

    def prepare(self, req: QueuedVideoRequest) -> PreparedJobAdmission:
        """只验证并固定入队参数，不触发供应商请求。"""
        pollinations = isinstance(req, PollinationsVideoRequest)
        provider = "pollinations" if pollinations else "agnes_video"
        smoke = queue_smoke_enabled()
        if (pollinations and smoke) or (not smoke and not self.provider_enabled_func(provider)):
            label = "Pollinations" if pollinations else "Agnes"
            raise VideoProviderDisabled(f"{label} video provider is disabled")
        if pollinations:
            if not resolve_live_provider_runtime_config(provider).api_key:
                raise VideoProviderDisabled("Pollinations video requires a configured API key")
            request_payload = _pollinations_request(req)
            input_mode = req.mode
            max_attempts = POLLINATIONS_VIDEO_MAX_ATTEMPTS
        else:
            request_payload = _canonical_request(req)
            input_mode = req.mode or ("image" if req.image or req.images else "text")
            max_attempts = self.policy.max_attempts
        request_hash, request_hash_version = request_hash_fields(
            build_video_request_hash_payload(req, provider=provider)
        )
        payload = {
            "schema_version": VIDEO_JOB_PAYLOAD_SCHEMA_VERSION,
            "pipeline": "video",
            "provider": provider,
            "input_mode": input_mode,
            "request": request_payload,
        }
        return PreparedJobAdmission.from_arguments(
            kind="video",
            stage="video_submit",
            request_hash=request_hash,
            request_hash_version=request_hash_version,
            payload=payload,
            provider=provider,
            model=req.model,
            prompt=req.prompt,
            payload_schema_version=VIDEO_JOB_PAYLOAD_SCHEMA_VERSION,
            max_attempts=max_attempts,
        )


def _pollinations_request(req: PollinationsVideoRequest) -> dict[str, Any]:
    payload = req.model_dump(exclude_none=True)
    if sanitize_job_value(payload) != payload or sanitize_error_text(req.prompt, limit=8000) != req.prompt:
        raise ValueError("Pollinations video request cannot be stored safely")
    for frame in (req.first_frame, req.last_frame):
        if frame is None:
            continue
        if "|" in frame:
            raise ValueError("Pollinations video reference URL contains an unsupported separator")
        decision = prepare_builtin_reference("pollinations", frame, model=req.model)
        if decision.kind == RELAY_REQUIRED:
            validate_gateway_image_reference(frame)
            if configured_reference_relay_backend() is None:
                raise ValueError("Pollinations video requires a configured relay for local images")
    return payload


def parse_video_job_payload(value: Any, *, provider: str = "agnes_video") -> tuple[QueuedVideoRequest, str]:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != VIDEO_JOB_PAYLOAD_SCHEMA_VERSION
        or value.get("pipeline") != "video"
        or value.get("provider") != provider
        or not isinstance(value.get("request"), dict)
    ):
        raise ValueError("invalid queued video payload")
    if provider == "pollinations":
        return PollinationsVideoRequest.model_validate(value["request"]), str(value.get("input_mode") or "text")
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


def is_worker_managed_video_job(job: dict[str, Any], *, conn=None, available_tables=None) -> bool:
    """损坏载荷不能把正式队列任务降格到无执行授权的旧查询入口。"""
    if (is_worker_managed_video_payload(job.get('input_json'))
                or job.get('stage') in {'video_submit', 'video_poll', 'asset_import'}
                or job.get('worker_kind') or job.get('dedupe_scope') == 'submission'):
        return True
    if not job.get('id'):
        return False
    if conn is None:
        with closing(db_connect()) as connection:
            return is_worker_managed_video_job(job, conn=connection, available_tables=available_tables)
    # 独立执行记录仍在时，损坏的载荷/阶段不能将正式任务降格为legacy查询。
    evidence = {'job_dispatches': 'job_id=?', 'job_attempts': 'job_id=?',
                'assistant_actions': "job_id=? AND status='submitted'", 'job_submission_keys': 'job_id=?'}
    # 离线升级盘点可传入同一读事务观察到的旧表清单；正式运行仍严格要求全部前置表。
    checks = [f'EXISTS(SELECT 1 FROM {table} WHERE {where})' for table, where in evidence.items()
              if available_tables is None or table in available_tables]
    return bool(checks and conn.execute('SELECT ' + ' OR '.join(checks),
                                       (job['id'],) * len(checks)).fetchone()[0])
