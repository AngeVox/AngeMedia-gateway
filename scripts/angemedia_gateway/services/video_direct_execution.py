"""即时API的真实执行记录与原子结果落盘；复用job和来源收据，不创建队列或重提请求。"""
import logging

from ..db.connection import db_transaction
from ..helpers import now_iso, safe_json
from ..media_execution_scope import MediaExecutionRejected
from ..providers.errors import ProviderTaskFailed
from ..reference_images import UnsafeImageReference
from ..repositories.assets import list_assets
from ..repositories.jobs import get_job, transition_job_in_connection
from ..repositories.video_job_origins import DIRECT_VIDEO_ORIGIN, direct_request_digest, bind_video_job_origin
from .video_submit_failure import classify_video_submit_failure
from .video_polling import video_output_summary, FAILED_PROVIDER_STATUSES
from .generation_assets import generated_asset_url
from .video_execution import VideoProviderDisabled

log = logging.getLogger('angemedia-gateway')


def begin_direct_video(request, *, lifecycle, started_at, request_hash, request_hash_version):
    summary = {name: getattr(request, name) for name in ('model','mode','height','width','num_frames','frame_rate',
        'seconds','size','aspect_ratio','wait_for_completion')}
    summary.update(origin_kind=DIRECT_VIDEO_ORIGIN, request_digest=direct_request_digest(request),
                   has_image=bool(request.image or request.images),
                   image_count=len(request.images) if request.images else int(bool(request.image)))
    job_id = lifecycle.create(kind='video', status='running', provider='agnes_video', model=request.model,
        prompt=request.prompt, input_json=safe_json(summary), started_at=started_at,
        request_hash=request_hash, request_hash_version=request_hash_version)
    return get_job(job_id)


def accept_direct_video(job, submitted):
    """实际任务号与来源摘要同事务保存；响应已受理后绝不回到生成入口。"""
    with db_transaction(immediate=True) as conn:
        bind_video_job_origin(conn, job['id'], submitted.task_id)
        return transition_job_in_connection(conn, job['id'], expected_version=job['version'], status='running',
            external_task_id=submitted.task_id, provider_status=submitted.provider_status,
            output_json=video_output_summary(task_id=submitted.task_id, provider_status=submitted.provider_status))


def note_direct_failure(job, error, *, accepted=False, prepared=True, result_pending=False):
    """尽力保存固定诊断；诊断写失败不能抹掉原取消或已受理任务的恢复身份。"""
    if accepted and isinstance(error, ProviderTaskFailed):
        code, category, status = 'video_generation_failed', 'provider_failure', 'failed'
    elif accepted:
        code = 'video_result_persistence_pending' if result_pending else 'video_wait_interrupted'
        if not job.get('external_task_id'): code = 'video_submit_persistence_pending'
        category, status = 'local_persistence' if result_pending else 'local_wait', 'running'
    elif isinstance(error, MediaExecutionRejected):
        code, category, status = error.code, 'provider_identity', 'failed'
    elif isinstance(error, VideoProviderDisabled):
        code, category, status = 'video_provider_disabled', 'provider_disabled', 'failed'
    elif not prepared or isinstance(error, UnsafeImageReference):
        code, category, status = 'video_execution_preparation_failed', 'invalid_request', 'failed'
    else:
        failure = classify_video_submit_failure(error)
        code, category = failure.error_code, failure.error_category
        # 未知结果继续占用原活动意图，普通请求重放不能因为本地超时而再次生成。
        status = 'running' if code == 'video_submit_ambiguous' else 'failed'
    try:
        with db_transaction(immediate=True) as conn:
            current = get_job(job['id'], conn=conn)
            if current is None or current['version'] != job['version'] or current['status'] != 'running':
                return
            fields = dict(error_code=code, error_message=code, error_category=category, human_hint='', retryable=0)
            if accepted and isinstance(error, ProviderTaskFailed) and error.provider_status in FAILED_PROVIDER_STATUSES:
                fields.update(provider_status=error.provider_status,
                              output_json=video_output_summary(task_id=job['external_task_id'], provider_status=error.provider_status))
            if status == 'failed': fields.update(stage='finalize', completed_at=now_iso())
            transition_job_in_connection(conn, job['id'], expected_version=job['version'], status=status, **fields)
    except Exception as persistence_error:
        log.warning('direct video diagnostic write failed: job_id=%s error_type=%s',job['id'],type(persistence_error).__name__)


def persist_direct_video(job, request, result, *, duration_ms, started_at, record, cache, save):
    """原响应策略不变；生成记录、缓存、作品与job结果一并提交。"""
    status = str(result.get('status') or 'completed')
    with db_transaction(immediate=True) as conn:
        record_id = record(media_type='video', prompt=request.prompt, enhanced_prompt=None, model=request.model,
            status=status, result=result, task_id=result['task_id'], provider='agnes_video', request_model=request.model,
            input_mode=request.mode or ('image' if request.image or request.images else 'text'),
            duration_ms=duration_ms, started_at=started_at, job_id=job['id'], conn=conn, direct_result=True)
        cache(result['task_id'], request.prompt, request.model, status, result, duration_ms=duration_ms, conn=conn)
        save(media_type='video', result=result, prompt=request.prompt, model=request.model, provider='agnes_video',
             duration_ms=duration_ms, job_id=job['id'], conn=conn)
        assets = [item for item in list_assets(job_id=job['id'], conn=conn) if generated_asset_url(item)]
        fields = dict(output_json=video_output_summary(task_id=result['task_id'], provider_status=status,
            asset_count=len(assets), history_id=record_id),
            provider_status=status, duration_ms=duration_ms)
        if request.wait_for_completion:
            fields.update(stage='finalize', completed_at=now_iso())
        transition_job_in_connection(conn, job['id'], expected_version=job['version'],
            status='succeeded' if request.wait_for_completion else 'running', **fields)
    return record_id
