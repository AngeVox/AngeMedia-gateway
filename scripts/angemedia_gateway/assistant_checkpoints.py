"""单机SQLite检查点的租约桥接；不执行模型，也不维护框架私有SQL。"""
from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .assistant_runtime_contract import RunLease, RunLeaseLost, require_owned_run
from .assistant_async import settle_on_cancel as _settle


class FencedSqliteSaver(BaseCheckpointSaver[str]):
    """绑定单个运行；所有数据库访问按运行库、检查点库的顺序加锁。"""

    def __init__(self, authority, delegate, lease: RunLease, clock: Callable[[], int], payload_guard=None):
        super().__init__(serde=delegate.serde)
        self._authority = authority
        self._delegate = delegate
        self._lease = lease
        self._clock = clock
        self._lock = asyncio.Lock()
        self._health = {"closed": False}
        self._payload_guard = payload_guard

    def _scope(self, config):
        if config is None or config.get("configurable", {}).get("thread_id") != self._lease.run_id:
            raise ValueError("检查点thread_id必须等于绑定的run_id")

    async def _verify(self):
        async with self._authority.execute(
            "SELECT * FROM assistant_runs WHERE id=? AND runtime_managed=1", (self._lease.run_id,),
        ) as cursor:
            run = await cursor.fetchone()
        async with self._authority.execute(
            "SELECT * FROM assistant_session_leases WHERE session_id=?", (self._lease.session_id,),
        ) as cursor:
            slot = await cursor.fetchone()
        require_owned_run(run, slot, self._lease, self._clock())
        async with self._authority.execute(
            "SELECT 1 FROM assistant_sessions WHERE id=? AND status='active'", (self._lease.session_id,),
        ) as cursor:
            if await cursor.fetchone() is None:
                raise RunLeaseLost("助手会话已归档或不存在")

    async def _transaction(self, operation):
        async with self._lock:
            if self._health["closed"]:
                raise RuntimeError("检查点连接已关闭")
            try:
                await self._authority.execute("BEGIN IMMEDIATE")
                await self._verify()
                # 第三方setup会隐式提交；它只能操作独立检查点连接，无法释放运行库锁。
                await self._delegate.setup()
                return await operation()
            except BaseException:
                try:
                    await self._delegate.conn.rollback()
                except BaseException:
                    self._health["closed"] = True
                    # 回滚失败时先关闭检查点连接，再释放权威锁；此实例不可继续使用。
                    await self._delegate.conn.close()
                raise
            finally:
                try:
                    await self._authority.rollback()
                except BaseException:
                    self._health["closed"] = True
                    await self._delegate.conn.close()
                    await self._authority.close()
                    raise

    async def _guard(self, operation):
        return await _settle(self._transaction(operation))

    async def aget_tuple(self, config):
        self._scope(config)
        return await self._guard(lambda: self._delegate.aget_tuple(config))

    async def alist(self, config, *, filter=None, before=None, limit=None):
        self._scope(config)
        if before is not None:
            self._scope(before)
        if limit is None:
            limit = 100
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("检查点列表数量必须是1到500的整数")

        async def collect():
            return [item async for item in self._delegate.alist(config, filter=filter, before=before, limit=limit)]

        # 不把数据库事务交给外部迭代器持有；单次读取有上限。
        for item in await self._guard(collect):
            yield item

    async def aput(self, config, checkpoint, metadata, new_versions):
        self._scope(config)
        async def write():
            if self._payload_guard is not None:
                self._payload_guard(checkpoint, self.serde)
                self._payload_guard(metadata, self.serde)
            return await self._delegate.aput(config, checkpoint, metadata, new_versions)
        return await self._guard(write)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        self._scope(config)
        async def write():
            if self._payload_guard is not None:
                for _channel, value in writes:
                    self._payload_guard(value, self.serde)
            await self._delegate.aput_writes(config, writes, task_id, task_path)
        await self._guard(write)

    def get_next_version(self, current, channel):
        return self._delegate.get_next_version(current, channel)

    def with_allowlist(self, extra_allowlist):
        clone = super().with_allowlist(extra_allowlist)
        if clone is not self:
            clone._delegate = copy.copy(self._delegate)
            clone._delegate.serde = clone.serde
        return clone


def checkpoint_paths(runtime_db: Path, checkpoint_db: Path):
    """运行写入和管理清理共用文件身份校验，拒绝路径别名及硬链接重合。"""
    runtime_db, checkpoint_db = Path(runtime_db).resolve(), Path(checkpoint_db).resolve()
    if runtime_db == checkpoint_db or (checkpoint_db.exists() and runtime_db.samefile(checkpoint_db)):
        raise ValueError("运行库与检查点库必须是独立文件")
    if not runtime_db.is_file():
        raise FileNotFoundError("运行数据库尚未初始化")
    return runtime_db, checkpoint_db


@asynccontextmanager
async def open_run_checkpoints(runtime_db: Path, checkpoint_db: Path, lease: RunLease,
                               *, clock: Callable[[], int] | None = None, payload_guard=None):
    """显式传入两个本地文件；只在功能入口启用时导入和打开，不创建运行库。"""
    runtime_db, checkpoint_db = checkpoint_paths(runtime_db, checkpoint_db)
    checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
    saver = None
    stack = AsyncExitStack()
    try:
        # enter_async_context在被取消前登记清理回调，防止连接创建期间遗留工作线程。
        authority = await _settle(stack.enter_async_context(aiosqlite.connect(
            runtime_db.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=5,
        )))
        authority.row_factory = aiosqlite.Row
        connection = await _settle(stack.enter_async_context(aiosqlite.connect(checkpoint_db, timeout=5)))
        delegate = AsyncSqliteSaver(connection, serde=JsonPlusSerializer(pickle_fallback=False))
        saver = FencedSqliteSaver(authority, delegate, lease, clock or (lambda: time.time_ns() // 1_000_000), payload_guard)
        yield saver
    finally:
        async def close():
            if saver is not None:
                async with saver._lock:
                    saver._health["closed"] = True
                    await stack.aclose()
            else:
                await stack.aclose()

        await _settle(close())
