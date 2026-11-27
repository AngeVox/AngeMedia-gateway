"""来源收据的原子持久化；仅保存摘要，外部编号只允许绑定一次。"""
from contextlib import closing
import hashlib
import json

from ..db.connection import db_connect
from ..helpers import now_iso
from ..media_execution_scope import MediaExecutionRejected
from ..security import validate_provider_external_id
from .assistant_action_execution import job_intent_digest
from ..job_submission import submission_intent_hash

DIRECT_VIDEO_ORIGIN = 'api_direct_v1'


def is_direct_video_job(job):
    try:
        payload = json.loads(job.get('input_json') or '{}')
        return job.get('kind') == 'video' and isinstance(payload, dict) and payload.get('origin_kind') == DIRECT_VIDEO_ORIGIN
    except (TypeError, ValueError):
        return False


def direct_request_digest(request):
    """私密请求只留完整意图摘要，不把内联图片或任意扩展字段写入任务正文。"""
    return submission_intent_hash(request.model_dump(mode='json'))


class VideoOriginRejected(MediaExecutionRejected):
    pass


def external_task_digest(task_id):
    return hashlib.sha256(validate_provider_external_id(task_id).encode('utf-8')).hexdigest()


def get_video_job_origin(job_id, *, conn=None):
    if conn is None:
        with closing(db_connect()) as connection:
            return get_video_job_origin(job_id, conn=connection)
    row = conn.execute('SELECT * FROM video_job_origins WHERE job_id=?', (job_id,)).fetchone()
    return dict(row) if row else None


def record_video_job_origin(conn, job, connection):
    """调用方在首次请求前的写事务记录真实尝试，不猜补已提交旧任务。"""
    if not conn.in_transaction:
        raise ValueError('来源收据需要写事务')
    if get_video_job_origin(job['id'], conn=conn) is not None:
        raise VideoOriginRejected('video_origin_submit_already_attempted')
    conn.execute('INSERT INTO video_job_origins VALUES(?,?,?,?,?,NULL,?,NULL)',
        (job['id'], 'agnes_video', job['model'], job_intent_digest(job), connection, now_iso()))
    return get_video_job_origin(job['id'], conn=conn)


def bind_video_job_origin(conn, job_id, task_id):
    if not conn.in_transaction:
        raise ValueError('来源绑定需要写事务')
    origin = get_video_job_origin(job_id, conn=conn)
    if origin is None:
        return
    digest = external_task_digest(task_id)
    if origin['external_task_digest'] is not None:
        if origin['external_task_digest'] != digest:
            raise VideoOriginRejected('video_origin_task_changed')
        return
    conn.execute('UPDATE video_job_origins SET external_task_digest=?,submitted_at=? WHERE job_id=?',
                 (digest, now_iso(), job_id))
