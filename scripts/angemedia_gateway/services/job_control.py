"""任务开始前的原子撤销；不终止运行中的请求，也不撤回上游任务或复用提交身份。"""
from ..helpers import now_iso
from ..repositories.jobs import transition_job_in_connection


class JobControlError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_job_version(value):
    if type(value) is not int or not 0 <= value <= 9223372036854775807:
        raise JobControlError('invalid_job_version')


def recovery_state(job):
    """恢复决策只用持久事实；retryable和诊断文案都不能授权重新生成。"""
    status, code = job.get('status'), job.get('error_code')
    if status in {'queued', 'running'}:
        return 'existing_task_in_progress' if job.get('external_task_id') else 'task_in_progress'
    if status == 'succeeded':
        return 'completed'
    if (status == 'canceled' and code == 'job_canceled_before_start'
            and not job.get('started_at') and not job.get('external_task_id')
            and not job.get('attempt_count')):
        return 'canceled_before_start'
    if status == 'failed':
        if job.get('external_task_id'):
            return 'provider_failed' if (job.get('kind'),code) in {('video','video_generation_failed'),('image','image_generation_failed')} else 'existing_task_unresolved'
        if code in {'video_submit_rejected', 'video_submit_queue_full', 'invalid_image_job_payload', 'invalid_video_job_payload',
                    'video_provider_disabled','video_execution_preparation_failed','image_submit_rejected'} or (job.get('kind') == 'video' and job.get('error_category') == 'action_authorization'
                    and str(code or '').startswith('action_execution_')):
            return 'submission_rejected'
        return 'submission_unresolved'
    return 'state_unavailable'


def regeneration_control(job):
    state = recovery_state(job)
    enabled = state in {'completed', 'canceled_before_start', 'submission_rejected', 'provider_failed'}
    return {'enabled': enabled, 'reason': None if enabled else state,
            'scope': 'new_proposal', 'requires_confirmation': True}


def cancellation_reason(conn, job):
    if job['status'] in {'succeeded', 'failed', 'canceled'}:
        return 'job_terminal'
    if (job['status'] != 'queued' or job.get('started_at') or job.get('external_task_id')
            or int(job.get('attempt_count') or 0) != 0):
        return 'job_already_started'
    if job.get('error_code') or job.get('claim_token') or job.get('claim_expires_at') or job.get('cancel_requested_at'):
        return 'job_state_not_cancelable'
    expected_stage = {'image': 'image_generate', 'video': 'video_submit'}.get(job['kind'])
    if not expected_stage or job['stage'] != expected_stage:
        return 'job_stage_not_cancelable'
    if conn.execute('SELECT 1 FROM job_attempts WHERE job_id=? LIMIT 1', (job['id'],)).fetchone():
        return 'job_already_started'
    return None


def cancel_before_start(conn, job, expected_version):
    """调用方必须持有同库写事务；与正式worker领取使用同一版本CAS边界。"""
    if not conn.in_transaction:
        raise ValueError('撤销需要调用方写事务')
    validate_job_version(expected_version)
    if job['status'] == 'canceled' and job.get('error_code') == 'job_canceled_before_start':
        return False, job
    if job['version'] != expected_version:
        raise JobControlError('job_version_conflict')
    reason = cancellation_reason(conn, job)
    if reason:
        raise JobControlError(reason)
    timestamp = now_iso()
    canceled = transition_job_in_connection(conn, job['id'], expected_version=expected_version,
        status='canceled', event_type='job_canceled_before_start', cancel_requested_at=timestamp,
        completed_at=timestamp, retryable=False, error_code='job_canceled_before_start')
    # 已领取/已发布的消息可迟到；保留其投递租约，worker会用终态/CAS拒绝执行。
    conn.execute("UPDATE job_dispatches SET status='failed',last_error='job_canceled_before_start',"
                 "updated_at=?,version=version+1 WHERE job_id=? AND status='pending'", (timestamp,job['id']))
    return True, canceled
