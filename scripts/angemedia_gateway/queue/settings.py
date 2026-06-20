"""Queue-only environment settings with an explicit disabled mode."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

WORKER_TASK_NAME = "angemedia.jobs.execute"


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or not value.strip() else value.strip()


def _bool(name: str, default: str) -> bool:
    value = _env(name, default).lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


def _int(name: str, default: str, *, minimum: int = 1) -> int:
    try:
        value = int(_env(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return value


def _float(name: str, default: str, *, minimum: float = 0.01) -> float:
    try:
        value = float(_env(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True)
class QueueSettings:
    enabled: bool = True
    backend: str = "celery"
    broker_url: str = field(default="redis://localhost:6379/0", repr=False)
    task_queue: str = "angemedia.jobs"
    worker_concurrency: int = 2
    dispatcher_interval_seconds: float = 1.0
    dispatcher_batch_size: int = 20
    dispatch_lease_seconds: int = 30
    dispatch_max_attempts: int = 10
    dispatch_retry_base_seconds: float = 2.0
    dispatch_retry_max_seconds: float = 60.0
    visibility_timeout_seconds: int = 3600

    def __post_init__(self) -> None:
        if self.enabled and self.backend != "celery":
            raise RuntimeError("QUEUE_ENABLED=true requires QUEUE_BACKEND=celery")
        if not self.enabled and self.backend != "disabled":
            raise RuntimeError("disabled mode requires QUEUE_BACKEND=disabled")
        if self.enabled and not self.broker_url.startswith(("redis://", "rediss://")):
            raise RuntimeError("Celery broker must use redis:// or rediss://")
        if not self.task_queue or any(ch.isspace() for ch in self.task_queue):
            raise RuntimeError("CELERY_TASK_QUEUE must be a non-empty queue name")
        for value, name in (
            (self.worker_concurrency, "WORKER_CONCURRENCY"),
            (self.dispatcher_batch_size, "QUEUE_DISPATCHER_BATCH_SIZE"),
            (self.dispatch_lease_seconds, "QUEUE_DISPATCH_LEASE_SECONDS"),
            (self.dispatch_max_attempts, "QUEUE_DISPATCH_MAX_ATTEMPTS"),
            (self.visibility_timeout_seconds, "CELERY_VISIBILITY_TIMEOUT_SECONDS"),
        ):
            if value < 1:
                raise RuntimeError(f"{name} must be positive")

    @classmethod
    def from_env(cls) -> "QueueSettings":
        redis_url = _env("REDIS_URL", "redis://localhost:6379/0")
        return cls(
            enabled=_bool("QUEUE_ENABLED", "true"),
            backend=_env("QUEUE_BACKEND", "celery").lower(),
            broker_url=_env("CELERY_BROKER_URL", redis_url),
            task_queue=_env("CELERY_TASK_QUEUE", "angemedia.jobs"),
            worker_concurrency=_int("WORKER_CONCURRENCY", "2"),
            dispatcher_interval_seconds=_float("QUEUE_DISPATCHER_INTERVAL_SECONDS", "1"),
            dispatcher_batch_size=_int("QUEUE_DISPATCHER_BATCH_SIZE", "20"),
            dispatch_lease_seconds=_int("QUEUE_DISPATCH_LEASE_SECONDS", "30"),
            dispatch_max_attempts=_int("QUEUE_DISPATCH_MAX_ATTEMPTS", "10"),
            dispatch_retry_base_seconds=_float("QUEUE_DISPATCH_RETRY_BASE_SECONDS", "2"),
            dispatch_retry_max_seconds=_float("QUEUE_DISPATCH_RETRY_MAX_SECONDS", "60"),
            visibility_timeout_seconds=_int("CELERY_VISIBILITY_TIMEOUT_SECONDS", "3600"),
        )

    def safe_summary(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "backend": self.backend,
            "task_queue": self.task_queue,
            "worker_concurrency": self.worker_concurrency,
        }
