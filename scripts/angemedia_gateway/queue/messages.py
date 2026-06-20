"""Strict, payload-free messages crossing the Redis/Celery boundary."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

MESSAGE_SCHEMA_VERSION = 1
EXECUTABLE_STAGES = frozenset({
    "image_generate", "video_submit", "video_poll", "asset_import", "finalize",
})
_ID_RE = re.compile(r"^[a-fA-F0-9]{32}$")
_MESSAGE_FIELDS = {
    "schema_version", "job_id", "stage", "attempt", "dispatch_id", "trace_id",
}


class InvalidQueueMessage(ValueError):
    def __init__(self) -> None:
        super().__init__("invalid queue message")


@dataclass(frozen=True)
class JobStageMessage:
    job_id: str
    stage: str
    attempt: int
    dispatch_id: str
    trace_id: str
    schema_version: int = MESSAGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != MESSAGE_SCHEMA_VERSION
        ):
            raise InvalidQueueMessage()
        if not isinstance(self.job_id, str) or not _ID_RE.fullmatch(self.job_id):
            raise InvalidQueueMessage()
        if not isinstance(self.stage, str) or self.stage not in EXECUTABLE_STAGES:
            raise InvalidQueueMessage()
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or not 1 <= self.attempt <= 1000:
            raise InvalidQueueMessage()
        if (
            not isinstance(self.dispatch_id, str)
            or not isinstance(self.trace_id, str)
            or not _ID_RE.fullmatch(self.dispatch_id)
            or not _ID_RE.fullmatch(self.trace_id)
        ):
            raise InvalidQueueMessage()

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


def parse_job_stage_message(value: Any) -> JobStageMessage:
    if not isinstance(value, Mapping) or set(value) - _MESSAGE_FIELDS:
        raise InvalidQueueMessage()
    required = _MESSAGE_FIELDS - {"schema_version"}
    if not required.issubset(value):
        raise InvalidQueueMessage()
    try:
        return JobStageMessage(
            schema_version=value.get("schema_version", MESSAGE_SCHEMA_VERSION),
            job_id=value["job_id"],
            stage=value["stage"],
            attempt=value["attempt"],
            dispatch_id=value["dispatch_id"],
            trace_id=value["trace_id"],
        )
    except (InvalidQueueMessage, TypeError, ValueError):
        raise InvalidQueueMessage() from None
