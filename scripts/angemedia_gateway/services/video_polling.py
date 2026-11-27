"""Pure video pipeline status decisions and bounded backoff policy."""
from __future__ import annotations

import math
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .. import config as C
from ..job_sanitizer import sanitized_json
from ..job_sanitizer import sanitize_error_text
from .video_execution import VideoPollResult

COMPLETED_PROVIDER_STATUSES = frozenset({"completed", "succeeded", "success", "done"})
FAILED_PROVIDER_STATUSES = frozenset({"failed", "error", "cancelled", "canceled"})


def stored_provider_status(job: dict[str, Any]) -> str:
    """读取已有服务方事实；本地失败/撤销和历史poll_error不能冒充上游结果。"""
    status = job.get('provider_status')
    if not status:
        try:
            output = json.loads(str(job.get('output_json') or '{}'))
            if isinstance(output, dict):
                status = output.get('provider_status') or output.get('status')
        except (TypeError, ValueError):
            pass
    if status:
        return 'unknown' if status == 'poll_error' else sanitize_error_text(str(status), limit=64) or 'unknown'
    if job.get('status') == 'succeeded':
        return 'completed'
    if job.get('status') in {'queued', 'running'} and job.get('external_task_id'):
        return 'submitted'
    return 'unknown'


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class VideoPipelinePolicy:
    poll_interval_seconds: float = C.AGNES_VIDEO_POLL_INTERVAL
    max_poll_seconds: float = C.AGNES_VIDEO_MAX_POLL_TIME
    max_attempts: int = 125
    max_backoff_seconds: float = 60.0
    now_func: Callable[[], datetime] = utc_now

    def __post_init__(self) -> None:
        if self.poll_interval_seconds <= 0 or self.max_poll_seconds <= 0:
            raise ValueError("video poll intervals must be positive")
        if self.max_attempts < 3 or self.max_attempts > 1000:
            raise ValueError("video pipeline max_attempts must be between 3 and 1000")

    @classmethod
    def from_config(cls) -> "VideoPipelinePolicy":
        interval = max(0.1, float(C.AGNES_VIDEO_POLL_INTERVAL))
        maximum = max(interval, float(C.AGNES_VIDEO_MAX_POLL_TIME))
        attempts = min(1000, max(3, math.ceil(maximum / interval) + 3))
        return cls(
            poll_interval_seconds=interval,
            max_poll_seconds=maximum,
            max_attempts=attempts,
        )

    def delay_seconds(self, attempt: int) -> float:
        exponent = max(0, min(int(attempt) - 1, 6))
        return min(self.max_backoff_seconds, self.poll_interval_seconds * (2 ** exponent))

    def available_at(self, attempt: int) -> str:
        return (self.now_func() + timedelta(seconds=self.delay_seconds(attempt))).isoformat()

    def exhausted(self, *, attempt: int, started_at: Any) -> bool:
        if attempt >= self.max_attempts:
            return True
        if not started_at:
            return False
        try:
            parsed = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (self.now_func() - parsed.astimezone(timezone.utc)).total_seconds() >= self.max_poll_seconds


def poll_decision(result: VideoPollResult) -> str:
    if result.provider_status in COMPLETED_PROVIDER_STATUSES:
        return "completed"
    if result.provider_status in FAILED_PROVIDER_STATUSES:
        return "failed"
    return "pending"


def video_output_summary(
    *,
    task_id: str | None,
    provider_status: str,
    asset_count: int = 0,
    asset_url: str | None = None,
    history_id: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "provider_status": provider_status,
        "localized": asset_count > 0,
        "has_asset": asset_count > 0,
        "asset_count": asset_count,
    }
    if task_id is not None:
        payload["task_id"] = task_id
    if asset_url and asset_url.startswith("/generated/"):
        payload["asset_url"] = asset_url
    if history_id:
        payload["history_id"] = history_id
    return sanitized_json(payload)
