"""Request hash admission and duplicate response helpers."""
from __future__ import annotations

from typing import Any, Iterable

from fastapi.responses import JSONResponse

from ..error_diagnostics import classify_duplicate_error
from ..repositories.jobs import find_recent_job_by_request_hash
from ..request_hash import compute_request_hash
from ..request_hash_builders import RequestHashBuildResult

REQUEST_HASH_VERSION = 1
IMAGE_REQUEST_HASH_VERSION = 2
IMAGE_ADMISSION_STATUSES = ("queued", "running")
VIDEO_ADMISSION_STATUSES = ("running",)


def request_hash_fields(
    result: RequestHashBuildResult,
    *,
    version: int = REQUEST_HASH_VERSION,
) -> tuple[str | None, int | None]:
    if result.payload is None:
        return None, None
    return compute_request_hash(result.payload, version=version), version


def duplicate_detail(job: dict[str, Any]) -> dict[str, Any]:
    classification = classify_duplicate_error()
    return {
        "code": "duplicate_in_flight_job",
        "error_category": classification["error_category"],
        "human_hint": classification["human_hint"],
        "retryable": classification["retryable"],
        "gateway_stage": classification["gateway_stage"],
        "existing_job": {
            "job_id": job.get("id"),
            "kind": job.get("kind"),
            "status": job.get("status"),
            "created_at": job.get("created_at"),
        },
    }


def duplicate_response_if_in_flight(
    *,
    kind: str,
    request_hash: str | None,
    request_hash_version: int | None,
    statuses: Iterable[str],
    alternate_hashes: Iterable[tuple[str | None, int | None]] = (),
) -> JSONResponse | None:
    status_values = tuple(statuses)
    candidates = [(request_hash, request_hash_version), *list(alternate_hashes)]
    seen: set[tuple[str, int]] = set()
    for candidate_hash, candidate_version in candidates:
        if not candidate_hash or candidate_version is None:
            continue
        key = (candidate_hash, int(candidate_version))
        if key in seen:
            continue
        seen.add(key)
        existing = find_recent_job_by_request_hash(
            kind=kind,
            request_hash=candidate_hash,
            request_hash_version=candidate_version,
            statuses=status_values,
        )
        if existing is not None:
            return JSONResponse(status_code=409, content={"detail": duplicate_detail(existing)})
    return None
