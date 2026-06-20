"""Stable contracts between job orchestration and a future broker adapter."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from .messages import JobStageMessage


@dataclass(frozen=True)
class QueueDispatchEnvelope:
    job_id: str
    job_kind: str
    stage: str
    payload_schema_version: int
    attempt: int = 1

    def as_dict(self) -> dict[str, str | int]:
        return asdict(self)


class QueueBackend(Protocol):
    """Delivery-only broker boundary; SQLite remains the job source of truth."""

    def publish(self, *, topic: str, message: JobStageMessage) -> str: ...

    def revoke(self, broker_message_id: str) -> None: ...

    def healthcheck(self) -> None: ...
