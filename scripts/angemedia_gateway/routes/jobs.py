"""Jobs 查询路由。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..runtime import require_auth
from ..security import redact_secret_text
from ..state import get_job, list_jobs

router = APIRouter()

VALID_KINDS = {"image", "video"}
VALID_STATUSES = {"queued", "running", "succeeded", "failed", "canceled"}


def _normalize_retryable(value):
    """Normalize retryable from DB int (0/1) to API bool."""
    if value is None:
        return None
    return bool(value)


LIST_COLUMNS = (
    "id,kind,status,provider,model,prompt,"
    "created_at,updated_at,started_at,completed_at,duration_ms,"
    "external_task_id,error_code,error_message,"
    "error_category,human_hint,retryable,gateway_stage"
)


def _validate_list_params(
    kind: Optional[str],
    status: Optional[str],
    limit: int,
    offset: int,
) -> None:
    if kind is not None and kind not in VALID_KINDS:
        raise HTTPException(status_code=400, detail=f"无效的 kind 参数，允许值：{', '.join(sorted(VALID_KINDS))}")
    if status is not None and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"无效的 status 参数，允许值：{', '.join(sorted(VALID_STATUSES))}")
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit 必须在 1-500 之间")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset 不能为负数")


def _job_list_item(job: dict[str, Any]) -> dict[str, Any]:
    """从 job dict 中提取列表所需字段（不含 input_json/output_json），脱敏 error_message。"""
    item = {col: job.get(col) for col in LIST_COLUMNS.split(",")}
    if item.get("error_message"):
        item["error_message"] = redact_secret_text(str(item["error_message"]))
    item["retryable"] = _normalize_retryable(item.get("retryable"))
    return item


@router.get("/v1/jobs", dependencies=[Depends(require_auth)])
async def list_jobs_endpoint(
    kind: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """查询 job 列表。"""
    _validate_list_params(kind, status, limit, offset)
    jobs = list_jobs(kind=kind, status=status, limit=limit, offset=offset)
    return {
        "object": "list",
        "data": [_job_list_item(j) for j in jobs],
        "limit": limit,
        "offset": offset,
    }


@router.get("/v1/jobs/{job_id}", dependencies=[Depends(require_auth)])
async def get_job_endpoint(job_id: str) -> dict[str, Any]:
    """查询单个 job 详情。"""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job 不存在")
    job.pop("request_hash", None)
    job.pop("request_hash_version", None)
    # 脱敏敏感字符串字段
    for field in ("input_json", "output_json", "error_message"):
        if job.get(field):
            job[field] = redact_secret_text(str(job[field]))
    job["retryable"] = _normalize_retryable(job.get("retryable"))
    return {"data": job}
