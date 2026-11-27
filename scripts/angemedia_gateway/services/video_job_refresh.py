"""On-demand Agnes video job polling and local asset import."""
from __future__ import annotations

import logging
import math
import json
from contextlib import nullcontext
from urllib.parse import urlparse
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from ..error_diagnostics import classify_provider_error
from ..db.connection import db_transaction
from ..helpers import now_iso, safe_json
from ..media import localize_video_result
from ..repositories.assets import list_assets
from ..repositories.jobs import get_job, transition_job_in_connection
from ..repositories.generations import record_generation, get_generation_by_job_id
from .. import config as C
from ..repositories.video_job_origins import is_direct_video_job, get_video_job_origin
from ..media_execution_scope import MediaExecutionRejected
from ..repositories.video_tasks import upsert_video_task
from ..runtime import agnes_video
from ..security import redact_secret_text, validate_provider_external_id
from ..video_models import is_agnes_video_modern
from .generation_assets import save_generated_asset, generated_asset_url
from .video_job_admission import is_worker_managed_video_job
from .video_polling import COMPLETED_PROVIDER_STATUSES, FAILED_PROVIDER_STATUSES, stored_provider_status
from .video_job_origin import video_job_execution

log = logging.getLogger("angemedia-gateway")

ACTIVE_JOB_STATUSES = {"queued", "running"}
TERMINAL_JOB_STATUSES = {"succeeded", "failed", "canceled"}
DEFAULT_MIN_POLL_INTERVAL_SECONDS = 10.0


class VideoJobRefreshError(RuntimeError):
    def __init__(self, status_code: int, detail: str | dict[str, str]) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _output_summary(
    *,
    task_id: str,
    provider_status: str,
    localized: bool,
    asset_count: int,
) -> str:
    return safe_json({
        "task_id": task_id,
        "provider_status": provider_status,
        "localized": localized,
        "has_asset": asset_count > 0,
        "asset_count": asset_count,
    })


def _normalized_poll_result(raw: dict[str, Any], task_id: str) -> dict[str, Any]:
    """Whitelist provider fields so upstream data cannot impersonate local state."""
    result: dict[str, Any] = {
        "task_id": task_id,
        "status": str(raw.get("status") or "unknown")[:64],
    }
    video_url = raw.get("video_url")
    if isinstance(video_url, str) and video_url:
        result["video_url"] = video_url
    if raw.get("duration_ms") is not None:
        try:
            result["duration_ms"] = max(0, int(raw["duration_ms"]))
        except (TypeError, ValueError):
            pass
    for field in ("error", "message"):
        if raw.get(field):
            result[field] = redact_secret_text(str(raw[field]))[:500]
    return result


def _synchronous_direct(job):
    return is_direct_video_job(job) and json.loads(job['input_json']).get('wait_for_completion') is True


class VideoJobRefreshService:
    """Poll one eligible video job once; never loops or schedules background work."""

    def __init__(
        self,
        *,
        poll_task_func: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        localize_video_result_func: Callable[..., Awaitable[dict[str, Any]]] = localize_video_result,
        upsert_video_task_func: Callable[..., None] = upsert_video_task,
        save_generated_asset_func: Callable[..., None] = save_generated_asset,
        list_assets_func: Callable[..., list[dict[str, Any]]] = list_assets,
        now_func: Callable[[], datetime] = _utc_now,
        min_poll_interval_seconds: float = DEFAULT_MIN_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.poll_task_func = poll_task_func or agnes_video.poll_task
        self.localize_video_result_func = localize_video_result_func
        self.upsert_video_task_func = upsert_video_task_func
        self.save_generated_asset_func = save_generated_asset_func
        self.list_assets_func = list_assets_func
        self.now_func = now_func
        self.min_poll_interval_seconds = max(0.0, float(min_poll_interval_seconds))

    async def refresh(self, job_id: str) -> dict[str, Any]:
        job = get_job(job_id)
        if job is None:
            raise VideoJobRefreshError(404, "Job 不存在")
        if job.get("kind") != "video":
            raise VideoJobRefreshError(400, "只有视频 Job 支持状态刷新")
        if job.get("provider") != "agnes_video":
            return self._response(job, refresh_status="unsupported", polled=False)
        if str(job.get("status") or "") in TERMINAL_JOB_STATUSES:
            assets = self.list_assets_func(job_id=job_id, limit=1, offset=0)
            response = self._response(
                job,
                refresh_status="terminal",
                polled=False,
                asset_url=self._asset_url(assets),
            )
            if _synchronous_direct(job) and job['status'] == 'succeeded' and not response['asset_url']:
                history = get_generation_by_job_id(job_id)
                if history and urlparse(history.get('result_url') or '').scheme in {'http','https'}:
                    response['video_url'] = history['result_url']
            return response
        if is_worker_managed_video_job(job):
            return self._response(job, refresh_status="worker_managed", polled=False)
        if str(job.get("status") or "") not in ACTIVE_JOB_STATUSES:
            return self._response(job, refresh_status="unsupported", polled=False)

        try:
            remote_only = _synchronous_direct(job) and not C.AUTO_DOWNLOAD_GENERATED
            task_id = validate_provider_external_id(str(job.get("external_task_id") or ""))
        except ValueError as exc:
            raise VideoJobRefreshError(409, "视频 Job 缺少有效的 provider task id") from exc
        retry_after = self._retry_after_seconds(job)
        if retry_after > 0:
            return self._response(
                job,
                refresh_status="throttled",
                polled=False,
                retry_after_seconds=retry_after,
            )

        try:
            model_name = str(job.get("model") or "")
            protected = is_direct_video_job(job) or get_video_job_origin(job['id']) is not None
            with video_job_execution(job, phase='followup') if protected else nullcontext():
                if is_agnes_video_modern(model_name):
                    raw_result = await self.poll_task_func(task_id, model_name=model_name)
                else:
                    raw_result = await self.poll_task_func(task_id)
        except MediaExecutionRejected as exc:
            raise VideoJobRefreshError(409, {'code': exc.code}) from None
        except Exception as exc:
            safe_error = redact_secret_text(str(exc))[:500]
            response = self._persist(job, None, {}, poll_error=safe_error)
            if response['refresh_status'] == 'superseded':
                return response
            log.warning("video job refresh poll failed: job_id=%s error_type=%s", job_id, type(exc).__name__)
            raise VideoJobRefreshError(502, "视频任务状态刷新失败，请稍后重试") from exc

        result = _normalized_poll_result(raw_result, task_id)

        provider_status = str(result.get("status") or "unknown").lower()[:64]
        if provider_status in COMPLETED_PROVIDER_STATUSES and not result.get("local_path") and not remote_only:
            try:
                result = await self.localize_video_result_func(result, force=True)
            except Exception as exc:
                # 下载失败不抹掉服务方已完成的事实；仍交现有download_pending处理。
                result = {**result, 'localize_error': redact_secret_text(str(exc))[:500]}

        return self._persist(job, provider_status, result, remote_only=remote_only)

    def _persist(self, job, provider_status, result, *, poll_error=None, remote_only=False):
        """外部读取不持写锁；所有可见记录同事务提交，迟到响应不覆盖新版本。"""
        with db_transaction(immediate=True) as conn:
            current = get_job(job['id'], conn=conn)
            if current is None:
                raise VideoJobRefreshError(404, 'Job 不存在')
            if (current['version'] != job['version'] or is_worker_managed_video_job(current, conn=conn)
                    or any(current[key] != job[key] for key in ('kind', 'provider', 'model', 'external_task_id', 'input_json',
                                                               'status', 'stage', 'worker_kind', 'dedupe_scope'))):
                assets = self.list_assets_func(job_id=job['id'], media_type='video', limit=1, conn=conn)
                return self._response(current, refresh_status='superseded', polled=True, asset_url=self._asset_url(assets))
            status, refresh_status, assets = 'running', 'polled', []
            fields = dict(error_code='', error_message='', error_category='', human_hint='', retryable=0,
                          gateway_stage='provider_poll', next_retry_at='')
            if poll_error is not None:
                classification = classify_provider_error(poll_error)
                fields.update(error_code='video_poll_failed', error_message=poll_error,
                              error_category=classification['error_category'], human_hint=classification['human_hint'], retryable=1)
            elif provider_status in FAILED_PROVIDER_STATUSES:
                status, refresh_status = 'failed', 'failed'
                fields.update(error_code='video_generation_failed', error_category='provider_failure', completed_at=now_iso(),
                    error_message=redact_secret_text(str(result.get('error') or result.get('message') or provider_status))[:500],
                    human_hint='视频服务商返回失败，请检查参数后重试。')
            elif provider_status in COMPLETED_PROVIDER_STATUSES and remote_only:
                # 原同步合同允许交付远端结果；关闭下载时不制造强制本地保存任务。
                status, refresh_status = 'succeeded', 'completed'
                fields.update(completed_at=now_iso())
            elif provider_status in COMPLETED_PROVIDER_STATUSES:
                # 局部写入失败也必须撤回资产元数据，不能留下半份“已保存”。
                conn.execute('SAVEPOINT legacy_video_assets')
                try:
                    if result.get('local_path'):
                        self.save_generated_asset_func(media_type='video', result=result, prompt=str(job.get('prompt') or ''),
                            model=job.get('model'), provider=job['provider'], duration_ms=int(result.get('duration_ms') or job.get('duration_ms') or 0),
                            job_id=job['id'], conn=conn)
                    assets = self.list_assets_func(job_id=job['id'], media_type='video', limit=1, conn=conn)
                    assets = [asset for asset in assets if generated_asset_url(asset)]
                    if not assets:
                        raise RuntimeError(result.get('localize_error') or '视频已完成，但本地下载尚未成功')
                    conn.execute('RELEASE legacy_video_assets')
                except Exception as exc:
                    conn.execute('ROLLBACK TO legacy_video_assets'); conn.execute('RELEASE legacy_video_assets')
                    assets, refresh_status = [], 'download_pending'
                    fields.update(error_code='video_download_pending', error_message=redact_secret_text(str(exc))[:500],
                        error_category='download_failure', human_hint='视频已生成，下载暂未完成；稍后再次刷新状态。',
                        retryable=1, gateway_stage='download')
                else:
                    status, refresh_status = 'succeeded', 'completed'
                    fields.update(completed_at=now_iso(), gateway_stage='asset_import')
            if provider_status is not None:
                fields.update(provider_status=provider_status, output_json=_output_summary(
                    task_id=job['external_task_id'], provider_status=provider_status, localized=bool(assets), asset_count=len(assets)))
                self.upsert_video_task_func(job['external_task_id'], str(job.get('prompt') or ''), str(job.get('model') or ''),
                    provider_status, result, duration_ms=int(result.get('duration_ms') or job.get('duration_ms') or 0), conn=conn)
                if is_direct_video_job(job):
                    record_generation(media_type='video',prompt=str(job.get('prompt') or ''),enhanced_prompt=None,
                        model=job.get('model'),status=provider_status,result=result,task_id=job['external_task_id'],
                        provider='agnes_video',request_model=job.get('model'),job_id=job['id'],conn=conn,direct_result=True,
                        duration_ms=int(result.get('duration_ms') or job.get('duration_ms') or 0))
            updated = transition_job_in_connection(conn, job['id'], expected_version=job['version'], status=status, **fields)
            response = self._response(updated, refresh_status=refresh_status, polled=True, asset_url=self._asset_url(assets))
            if remote_only and status == 'succeeded' and result.get('video_url'):
                response['video_url'] = result['video_url']
            return response

    def _retry_after_seconds(self, job: dict[str, Any]) -> int:
        updated_at = _parse_datetime(job.get("updated_at"))
        if updated_at is None or self.min_poll_interval_seconds <= 0:
            return 0
        elapsed = max(0.0, (self.now_func() - updated_at).total_seconds())
        return max(0, math.ceil(self.min_poll_interval_seconds - elapsed))

    @staticmethod
    def _asset_url(assets: list[dict[str, Any]]) -> str | None:
        if not assets:
            return None
        return generated_asset_url(assets[0])

    @staticmethod
    def _response(
        job: dict[str, Any],
        *,
        refresh_status: str,
        polled: bool,
        provider_status: str | None = None,
        retry_after_seconds: int = 0,
        asset_url: str | None = None,
    ) -> dict[str, Any]:
        return {
            "job_id": str(job.get("id") or ""),
            "status": str(job.get("status") or "unknown"),
            "provider_status": provider_status or stored_provider_status(job),
            "refresh_status": refresh_status,
            "polled": polled,
            "retry_after_seconds": retry_after_seconds,
            "asset_url": asset_url,
        }
