"""异步图片提交收据；只保存来源摘要和取回结果所需事实，不存储密钥。"""
from contextlib import closing
import json

from ..db.connection import db_connect
from ..helpers import now_iso
from ..media_execution_scope import MediaExecutionRejected
from ..security import validate_provider_external_id
from .assistant_action_execution import job_intent_digest


def image_task_receipts(job_id, *, conn=None):
    if conn is None:
        with closing(db_connect()) as connection:
            return image_task_receipts(job_id, conn=connection)
    return [dict(row) for row in conn.execute(
        'SELECT * FROM image_task_receipts WHERE job_id=? ORDER BY route_index', (job_id,))]


def accepted_image_task(job_id, *, conn=None):
    return next((row for row in image_task_receipts(job_id, conn=conn) if row['state']=='accepted'), None)


def record_image_submission(conn, job, route_index, connection):
    """必须在POST之前的写事务中占据本条路由；重复投递不能再次提交。"""
    if not conn.in_transaction:
        raise ValueError('图片提交收据需要写事务')
    payload=json.loads(job['input_json'])
    route=payload['route']['routes'][route_index]
    if job['kind']!='image' or route['provider']!='modelscope' or payload.get('async_image',{}).get('version')!=1:
        raise MediaExecutionRejected('image_task_route_invalid')
    if any(row['state']!='rejected' for row in image_task_receipts(job['id'],conn=conn)):
        raise MediaExecutionRejected('image_task_submit_already_attempted')
    conn.execute('INSERT INTO image_task_receipts VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL,?)',
        (job['id'],route_index,route['provider'],route['model'],job_intent_digest(job),connection,'prepared',now_iso()))
    return image_task_receipts(job['id'],conn=conn)[-1]


def finish_image_submission(conn, receipt, *, task_id=None, rejected=False):
    if not conn.in_transaction:
        raise ValueError('图片提交收据需要写事务')
    state='accepted' if task_id is not None else 'rejected' if rejected else 'uncertain'
    if task_id is not None:
        if not isinstance(task_id,str): raise ValueError('图片任务编号类型无效')
        validate_provider_external_id(task_id)
    cursor=conn.execute('UPDATE image_task_receipts SET state=?,task_id=? WHERE job_id=? AND route_index=? '
        "AND state IN ('prepared','uncertain') AND intent_digest=? AND connection_digest=?",
        (state,task_id,receipt['job_id'],receipt['route_index'],receipt['intent_digest'],receipt['connection_digest']))
    if cursor.rowcount!=1:
        current=accepted_image_task(receipt['job_id'],conn=conn)
        if state!='accepted' or not current or current['task_id']!=task_id or current['route_index']!=receipt['route_index']:
            raise MediaExecutionRejected('image_task_receipt_changed')


def record_image_observation(conn, receipt, status, result_url=None):
    """只写原编号的观察，不把本地失败覆盖成上游FAILED。"""
    cursor=conn.execute('UPDATE image_task_receipts SET provider_status=?,result_url=COALESCE(?,result_url) '
        "WHERE job_id=? AND route_index=? AND state='accepted' AND task_id=? "
        "AND (provider_status IS NULL OR provider_status NOT IN ('SUCCEED','FAILED') OR provider_status=?)",
        (status,result_url,receipt['job_id'],receipt['route_index'],receipt['task_id'],status))
    if cursor.rowcount!=1:
        raise MediaExecutionRejected('image_task_receipt_changed')
