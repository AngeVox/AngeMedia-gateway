"""Celery worker process entrypoint for the formal Linux container runtime."""
from __future__ import annotations

import argparse
import sys

from ..db.schema import init_db
from ..queue.celery_app import celery_app
from ..queue.settings import QueueSettings


def default_worker_pool() -> str:
    return "solo" if sys.platform.startswith("win") else "prefork"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the AngeMedia Celery worker")
    parser.add_argument("--loglevel", default="INFO")
    parser.add_argument("--pool", default=default_worker_pool(), help="Celery worker pool; Windows defaults to solo")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = QueueSettings.from_env()
    if not settings.enabled:
        raise RuntimeError("worker cannot start while queue is disabled")
    init_db()
    celery_app.worker_main([
        "worker",
        f"--loglevel={args.loglevel}",
        f"--concurrency={settings.worker_concurrency}",
        f"--queues={settings.task_queue}",
        f"--pool={args.pool}",
        "--without-gossip",
        "--without-mingle",
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
