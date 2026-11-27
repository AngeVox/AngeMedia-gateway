"""Video generation orchestration."""
from __future__ import annotations

import time
import asyncio
import sqlite3
from collections.abc import Callable
from typing import Any

from ..helpers import now_iso
from ..media import localize_video_result
from ..repositories.generations import record_generation
from ..repositories.jobs import get_job_by_external_task_id
from ..repositories.assets import list_assets
from ..repositories.settings import builtin_provider_enabled
from ..repositories.video_tasks import upsert_video_task, get_video_task
from ..reference_images import UnsafeImageReference
from ..request_hash_builders import build_video_request_hash_payload
from ..schemas import VideoRequest
from ..security import validate_task_id
from ..providers.errors import ProviderTaskFailed
from ..media_execution_scope import MediaExecutionRejected
from .video_submit_failure import classify_video_submit_failure
from .generation_assets import save_generated_asset, generated_asset_url
from .job_lifecycle import JobLifecycle
from .request_dedupe import VIDEO_ADMISSION_STATUSES, duplicate_response_if_in_flight, request_hash_fields
from .video_execution import VideoExecutionService, VideoProviderDisabled
from .video_job_admission import is_worker_managed_video_job
from .video_polling import stored_provider_status
from .video_job_refresh import VideoJobRefreshService, VideoJobRefreshError
from .video_references import InvalidVideoReference, validate_video_reference_sources as _validate_reference_sources
from .video_job_origin import video_job_execution
from .video_direct_execution import begin_direct_video, accept_direct_video, note_direct_failure, persist_direct_video


async def create_video(
    req: VideoRequest,
    *,
    agnes_video_provider: Any,
    builtin_provider_enabled_func: Callable[[str], bool] = builtin_provider_enabled,
    localize_video_result_func: Callable[..., Any] = localize_video_result,
    record_generation_func: Callable[..., str] = record_generation,
    upsert_video_task_func: Callable[..., None] = upsert_video_task,
    save_generated_asset_func: Callable[..., None] = save_generated_asset,
    job_lifecycle: JobLifecycle | None = None,
) -> dict[str, Any]:
    lifecycle = job_lifecycle or JobLifecycle()
    req = req.model_copy(deep=True)
    if not builtin_provider_enabled_func("agnes_video"):
        raise VideoProviderDisabled("Agnes 视频渠道已停用，请在管理后台恢复后再生成")
    _validate_reference_sources(req)

    started_at = now_iso()
    started = time.perf_counter()
    request_hash: str | None = None
    request_hash_version: int | None = None
    if not req.wait_for_completion:
        request_hash, request_hash_version = request_hash_fields(
            build_video_request_hash_payload(req, provider="agnes_video")
        )
        duplicate_response = duplicate_response_if_in_flight(
            kind="video",
            request_hash=request_hash,
            request_hash_version=request_hash_version,
            statuses=VIDEO_ADMISSION_STATUSES,
        )
        if duplicate_response is not None:
            return duplicate_response
    try:
        job = begin_direct_video(req, lifecycle=lifecycle, started_at=started_at,
                                 request_hash=request_hash, request_hash_version=request_hash_version)
    except sqlite3.IntegrityError:
        duplicate = duplicate_response_if_in_flight(kind='video', request_hash=request_hash,
            request_hash_version=request_hash_version, statuses=VIDEO_ADMISSION_STATUSES)
        if duplicate is not None: return duplicate
        raise
    submitted = None
    prepared = False
    try:
        with video_job_execution(job, request=req):
            prepared = True
            submitted = await VideoExecutionService(provider=agnes_video_provider,
                provider_enabled_func=builtin_provider_enabled_func).submit(req)
        job = accept_direct_video(job, submitted)
        if req.wait_for_completion:
            with video_job_execution(job, phase='followup'):
                result = await agnes_video_provider.wait_for_video(submitted.task_id, model_name=req.model)
            result = await localize_video_result_func(result)
        else:
            result = {"task_id": submitted.task_id, "status": submitted.provider_status}
    except asyncio.CancelledError as error:
        note_direct_failure(job, error, accepted=submitted is not None, prepared=prepared)
        raise
    except UnsafeImageReference as error:
        note_direct_failure(job, error, accepted=submitted is not None, prepared=prepared)
        raise InvalidVideoReference("reference image cannot be safely materialized") from error
    except Exception as error:
        note_direct_failure(job, error, accepted=submitted is not None, prepared=prepared)
        if submitted is not None:
            code = 'video_wait_interrupted' if job.get('external_task_id') else 'video_submit_persistence_pending'
            if isinstance(error, ProviderTaskFailed): code = 'video_generation_failed'
            raise VideoJobRefreshError(502, {'code': code, 'job_id': job['id'],
                                           'task_id': submitted.task_id, 'retryable': False}) from None
        if isinstance(error, VideoProviderDisabled): raise
        if isinstance(error, MediaExecutionRejected):
            raise VideoJobRefreshError(409, {'code': error.code, 'job_id': job['id'], 'retryable': False}) from None
        failure = classify_video_submit_failure(error)
        raise VideoJobRefreshError(502, {'code': failure.error_code if prepared else 'video_execution_preparation_failed', 'job_id': job['id'],
            'error_category': failure.error_category,
            'human_hint': failure.human_hint if prepared and failure.error_code != 'video_submit_ambiguous' else '',
            'gateway_stage': 'video_submit', 'retryable': False}) from None

    duration_ms = int((time.perf_counter() - started) * 1000)
    result["provider"] = "agnes_video"
    result["model"] = req.model
    result["duration_ms"] = duration_ms
    result['task_id'] = submitted.task_id
    try:
        record_id = persist_direct_video(job, req, result, duration_ms=duration_ms, started_at=started_at,
            record=record_generation_func, cache=upsert_video_task_func, save=save_generated_asset_func)
    except Exception as error:
        note_direct_failure(job,error,accepted=True,result_pending=True)
        raise VideoJobRefreshError(502, {'code':'video_result_persistence_pending','job_id':job['id'],
                                        'task_id':submitted.task_id,'retryable':False}) from None
    result["history_id"] = record_id
    if not req.wait_for_completion:
        result["job_id"] = job['id']
    return result


async def get_video(
    task_id: str,
    *,
    agnes_video_provider: Any,
    localize_video_result_func: Callable[..., Any] = localize_video_result,
    upsert_video_task_func: Callable[..., None] = upsert_video_task,
    get_job_by_external_task_id_func: Callable[..., dict[str, Any] | None] = get_job_by_external_task_id,
    save_generated_asset_func: Callable[..., None] = save_generated_asset,
) -> dict[str, Any]:
    safe_task_id = validate_task_id(task_id)
    job = get_job_by_external_task_id_func(safe_task_id, kind="video")
    if job is None:
        # 没有本地身份不能用当前凭据猜原账户；清理任务也不能重新打开外部查询。
        cached = get_video_task(safe_task_id)
        if cached is None:
            raise VideoJobRefreshError(404, {'code': 'video_task_record_unavailable'})
        url = generated_asset_url({'url_path': cached.get('video_url')})
        result = {'task_id': safe_task_id, 'status': stored_provider_status({'provider_status': cached.get('status')}),
                  'job_status': 'record_unavailable', 'record_only': True, 'origin_verified': False, 'localized': bool(url)}
        if url:
            result['video_url'] = url
        return result
    if job and is_worker_managed_video_job(job):
        # 正式队列任务只能由原worker继续收尾；兼容GET只读取状态和关联本地作品。
        result = {'task_id': safe_task_id, 'status': stored_provider_status(job),
                  'job_id': job['id'], 'job_status': job['status'], 'worker_managed': True}
        if job['status'] == 'succeeded':
            assets = list_assets(job_id=job['id'], media_type='video', limit=1)
            if assets:
                url = generated_asset_url(assets[0])
                if url:
                    result.update(video_url=url, localized=True)
        return result
    if job:
        refreshed = await VideoJobRefreshService(
            poll_task_func=agnes_video_provider.poll_task,
            localize_video_result_func=localize_video_result_func,
            upsert_video_task_func=upsert_video_task_func,
            save_generated_asset_func=save_generated_asset_func,
            min_poll_interval_seconds=0,
        ).refresh(job['id'])
        result = {'task_id': safe_task_id, 'job_id': job['id'], 'job_status': refreshed['status'],
                  'status': refreshed['provider_status'], 'refresh_status': refreshed['refresh_status'],
                  'localized': bool(refreshed['asset_url'])}
        if refreshed['asset_url']:
            result['video_url'] = refreshed['asset_url']
        elif refreshed.get('video_url'):
            result['video_url'] = refreshed['video_url']
        return result
