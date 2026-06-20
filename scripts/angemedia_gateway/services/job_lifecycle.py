"""Strict job lifecycle service backed by versioned compare-and-swap updates."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..repositories.jobs import (
    InvalidJobTransitionError,
    JobNotFoundError,
    StaleJobVersionError,
    create_job,
    get_job,
    transition_job,
)

InvalidJobTransition = InvalidJobTransitionError
StaleJobVersion = StaleJobVersionError

CreateJobFunc = Callable[..., dict[str, Any]]
UpdateJobFunc = Callable[..., dict[str, Any]]
log = logging.getLogger("angemedia-gateway")


@dataclass
class JobLifecycle:
    create_job_func: CreateJobFunc = create_job
    update_job_func: UpdateJobFunc = transition_job
    logger: logging.Logger = field(default=log)

    def create(self, **kwargs: Any) -> str:
        job = self.create_job_func(**kwargs)
        if not job or not job.get("id"):
            raise RuntimeError("job repository did not return a job id")
        return str(job["id"])

    def transition(
        self,
        job_id: str,
        *,
        expected_version: int,
        status: str,
        stage: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self.update_job_func(
            job_id,
            expected_version=expected_version,
            status=status,
            stage=stage,
            **kwargs,
        )

    def _transition_current(self, job_id: str, *, status: str, **kwargs: Any) -> dict[str, Any]:
        job = get_job(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return self.transition(
            job_id,
            expected_version=int(job["version"]),
            status=status,
            **kwargs,
        )

    def mark_running(
        self,
        job_id: str,
        *,
        kind: str,
        provider: str,
        model: str | None,
        started_at: str,
    ) -> dict[str, Any]:
        return self._transition_current(
            job_id,
            status="running",
            provider=provider,
            model=model,
            started_at=started_at,
        )

    def mark_succeeded(
        self,
        job_id: str,
        *,
        kind: str,
        output_json: str | None = None,
        completed_at: str,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        return self._transition_current(
            job_id,
            status="succeeded",
            output_json=output_json,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )

    def mark_failed(
        self,
        job_id: str,
        *,
        kind: str,
        error_code: str,
        error_message: str,
        completed_at: str,
        error_category: str | None = None,
        human_hint: str | None = None,
        retryable: int | None = None,
        gateway_stage: str | None = None,
    ) -> dict[str, Any]:
        return self._transition_current(
            job_id,
            status="failed",
            error_code=error_code,
            error_message=error_message,
            error_category=error_category,
            human_hint=human_hint,
            retryable=retryable,
            gateway_stage=gateway_stage,
            completed_at=completed_at,
        )
