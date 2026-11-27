"""提交键与任务的一次性绑定；使用调用方事务，不管理授权或队列投递。"""
from __future__ import annotations

from ..helpers import now_iso
from ..job_submission import JobSubmissionConflict, JobSubmissionGone, JobSubmissionKey
from .jobs import get_job


def replay_submission(conn, key: JobSubmissionKey, intent_hash: str):
    row = conn.execute('SELECT intent_hash,job_id FROM job_submission_keys WHERE namespace=? AND submission_key=?',
                       (key.namespace, key.value)).fetchone()
    if row is None:
        return None
    if row['intent_hash'] != intent_hash:
        raise JobSubmissionConflict('提交键已绑定其他生成意图')
    job = get_job(row['job_id'], conn=conn)
    if job is None:
        raise JobSubmissionGone(row['job_id'])
    return job


def bind_submission(conn, key: JobSubmissionKey, intent_hash: str, job_id: str):
    conn.execute('INSERT INTO job_submission_keys(namespace,submission_key,intent_hash,job_id,created_at) VALUES(?,?,?,?,?)',
                 (key.namespace, key.value, intent_hash, job_id, now_iso()))
