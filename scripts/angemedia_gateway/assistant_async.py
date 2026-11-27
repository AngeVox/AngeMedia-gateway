"""异步运行的数据库收尾原语；不依赖模型框架。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


async def settle_on_cancel(operation: Awaitable[T]) -> T:
    """取消后等待已开始的操作收尾，再把取消传给调用者；不丢弃线程中的提交。"""
    task = asyncio.ensure_future(operation)
    canceled = None
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError as exc:
            if task.cancelled():
                raise
            canceled = exc
        except BaseException:
            if canceled is not None:
                raise canceled
            raise
    if canceled is not None:
        raise canceled
    return result
