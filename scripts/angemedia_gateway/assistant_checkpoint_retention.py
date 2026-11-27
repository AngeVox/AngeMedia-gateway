"""独立管理检查点保留；只清理超过保留期的终态或归档孤儿，不裁剪活跃父链。"""
from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path
import re
import time

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .assistant_async import settle_on_cancel
from .assistant_checkpoints import checkpoint_paths
from .assistant_runtime_contract import TERMINAL_RUN_STATUSES


async def prune_run_checkpoints(runtime_db: Path, checkpoint_db: Path, *, after: str = "", limit: int = 50,
                                retention_days: int = 7, orphan_days: int = 1, now_ms: int | None = None):
    """一页扫描返回游标；删除委托框架API，运行库写锁覆盖资格复核和删除提交。"""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("检查点清理每页必须是1到100个运行")
    if any(type(value) is not int or not 1 <= value <= 365 for value in (retention_days, orphan_days)):
        raise ValueError("检查点保留期必须是1到365天")
    if not isinstance(after, str) or len(after) > 128:
        raise ValueError("清理游标非法")
    runtime_db, checkpoint_db = checkpoint_paths(runtime_db, checkpoint_db)
    if not checkpoint_db.exists():
        return {"scanned": 0, "deleted": 0, "after": ""}
    now = time.time_ns() // 1_000_000 if now_ms is None else now_ms

    def expired(timestamp, days):
        try:
            parsed = datetime.fromisoformat(timestamp)
            if parsed.tzinfo is None:
                return False
            return parsed.astimezone(timezone.utc).timestamp() * 1000 <= now - days * 86400000
        except (ValueError, TypeError, OverflowError):
            return False

    async def sweep():
        stack = AsyncExitStack()
        try:
            authority = await stack.enter_async_context(aiosqlite.connect(runtime_db.as_uri()+"?mode=rw", uri=True, isolation_level=None))
            authority.row_factory = aiosqlite.Row
            connection = await stack.enter_async_context(aiosqlite.connect(checkpoint_db))
            try:
                await authority.execute("BEGIN IMMEDIATE")
                saver = AsyncSqliteSaver(connection)
                await saver.setup()
                # 仅按固定版本的索引列发现thread；删除格式/事务仍由框架维护。
                async with connection.execute(
                    "SELECT thread_id FROM (SELECT thread_id FROM checkpoints UNION SELECT thread_id FROM writes) "
                    "WHERE thread_id>? ORDER BY thread_id LIMIT ?", (after, limit),
                ) as cursor:
                    ids = [row[0] for row in await cursor.fetchall()]
                deleted = 0
                for run_id in ids:
                    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
                        continue
                    async with authority.execute("SELECT status,completed_at,runtime_managed FROM assistant_runs WHERE id=?", (run_id,)) as cursor:
                        run = await cursor.fetchone()
                    eligible = False
                    if run is not None:
                        eligible = run["runtime_managed"] == 1 and run["status"] in TERMINAL_RUN_STATUSES and expired(run["completed_at"], retention_days)
                    else:
                        latest = await saver.aget_tuple({"configurable": {"thread_id": run_id, "checkpoint_ns": ""}})
                        eligible = latest is not None and expired(latest.checkpoint.get("ts"), orphan_days)
                    if eligible:
                        await saver.adelete_thread(run_id)
                        deleted += 1
                return {"scanned": len(ids), "deleted": deleted, "after": ids[-1] if len(ids) == limit else ""}
            finally:
                # 先结束检查点连接，再释放运行库锁；失败时不留下晚到提交。
                await connection.close()
                await authority.rollback()
        finally:
            await stack.aclose()

    return await settle_on_cancel(sweep())
