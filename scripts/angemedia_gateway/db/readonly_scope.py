"""任务局部的只读数据库绑定；不修改进程配置，不影响并发运行。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_READ_DATABASE: ContextVar[Path | None] = ContextVar("angemedia_read_database", default=None)


@contextmanager
def readonly_database(path: Path):
    target = Path(path).resolve(strict=True)
    existing = _READ_DATABASE.get()
    if existing is not None and existing != target:
        raise ValueError("只读调用不能切换数据库")
    token = _READ_DATABASE.set(target)
    try:
        yield
    finally:
        _READ_DATABASE.reset(token)


def readonly_target(path: Path | None) -> Path | None:
    target = _READ_DATABASE.get()
    if target is not None and path is not None and Path(path).resolve() != target:
        raise ValueError("只读调用不能访问其他数据库")
    return target
