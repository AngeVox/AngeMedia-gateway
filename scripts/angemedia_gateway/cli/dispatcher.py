"""Dedicated transactional-outbox dispatcher process."""
from __future__ import annotations

import argparse
import logging
import signal
import time

from ..db.schema import init_db
from ..queue.celery_app import celery_app
from ..queue.celery_backend import CeleryQueueBackend
from ..queue.diagnostics import queue_diagnostics
from ..queue.settings import QueueSettings
from ..services.job_dispatcher import JobDispatcher

log = logging.getLogger("angemedia-gateway")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish AngeMedia outbox rows to Celery")
    parser.add_argument("--once", action="store_true", help="Dispatch one batch and exit")
    parser.add_argument("--check", action="store_true", help="Check broker connectivity and exit")
    parser.add_argument("--interval", type=float, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = QueueSettings.from_env()
    if not settings.enabled:
        raise RuntimeError("dispatcher cannot start while queue is disabled")
    backend = CeleryQueueBackend(app=celery_app, settings=settings)
    if args.check:
        diagnostics = queue_diagnostics(backend, settings)
        if diagnostics["healthy"]:
            log.info("queue broker healthy")
            return 0
        log.error("queue broker unavailable")
        return 1

    init_db()
    dispatcher = JobDispatcher(
        queue_backend=backend,
        batch_size=settings.dispatcher_batch_size,
        lease_seconds=settings.dispatch_lease_seconds,
        max_attempts=settings.dispatch_max_attempts,
        retry_base_seconds=settings.dispatch_retry_base_seconds,
        retry_max_seconds=settings.dispatch_retry_max_seconds,
    )
    if args.once:
        dispatcher.dispatch_once()
        return 0

    interval = args.interval if args.interval is not None else settings.dispatcher_interval_seconds
    if interval <= 0:
        raise RuntimeError("dispatcher interval must be positive")
    stopping = False

    def request_stop(*_args: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    while not stopping:
        try:
            dispatcher.dispatch_once()
        except Exception as exc:
            log.warning("dispatcher batch failed: error_type=%s", type(exc).__name__)
        if not stopping:
            time.sleep(interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
