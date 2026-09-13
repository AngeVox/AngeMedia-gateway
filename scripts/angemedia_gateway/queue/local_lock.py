"""Single-consumer process lock for the brokerless local queue."""
from __future__ import annotations

import os
from pathlib import Path
from typing import IO

from .. import config as C

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback below
    fcntl = None


class LocalQueueAlreadyRunning(RuntimeError):
    pass


class LocalQueueProcessLock:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(C.DB_FILE).with_name("local-queue.lock")
        self._handle: IO[str] | None = None

    def __enter__(self) -> "LocalQueueProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        if fcntl is None:
            handle.close()
            raise RuntimeError("local queue process locking requires POSIX flock")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise LocalQueueAlreadyRunning("local queue dispatcher is already running") from None
        handle.seek(0)
        handle.truncate(0)
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
