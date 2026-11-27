"""管理员在确认服务端证据后追加一条旧任务审阅。"""
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Response
from pydantic import BaseModel, ConfigDict, Field

from ..assistant_run_access import RunAuthenticationExpired
from ..services import legacy_job_reviews as reviews
from ..services.legacy_job_evidence import LegacyReviewError
from .admin_access import AdminSessionAccess

router = APIRouter(prefix='/v1/admin/jobs')
JobId = Annotated[str, Path(pattern=r'^[a-f0-9]{32}$')]


class ReviewConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    request_key: str = Field(min_length=32, max_length=36)
    evidence_digest: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.get('/{job_id}/legacy-review')
def get_review(job_id: JobId, response: Response, access: AdminSessionAccess):
    response.headers['Cache-Control'] = 'no-store'
    return _call(reviews.inspect_legacy_job_review, job_id, access=access)


@router.post('/{job_id}/legacy-review')
def post_review(job_id: JobId, payload: ReviewConfirmation, access: AdminSessionAccess):
    return _call(reviews.record_legacy_job_review, job_id, request_key=payload.request_key,
                 evidence_digest=payload.evidence_digest, access=access)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except RunAuthenticationExpired:
        raise HTTPException(401, detail={'code': 'authentication_expired'}) from None
    except LegacyReviewError as exc:
        raise HTTPException(exc.status, detail={'code': exc.code}) from None
