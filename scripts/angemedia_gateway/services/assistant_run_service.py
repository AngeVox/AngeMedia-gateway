"""账户运行服务：持久提交/观察/取消与应用拥有的后台恢复分离。"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..assistant_async import settle_on_cancel
from ..assistant_run_access import RunAccess
from ..repositories.assistant_runtime import AssistantRunRepository
from .assistant_run_supervisor import AssistantRunSupervisor, Runner

LOGGER = logging.getLogger(__name__)


class AssistantRunService:
    def __init__(self, repository: AssistantRunRepository, *, runner: Runner | None = None,
                 checkpoint_db: Path | None = None, concurrency: int = 4, recovery_interval_ms: int = 1000,
                 lease_ms: int = 30000, heartbeat_ms: int = 1000, retention_interval: float = 60,
                 pruner=None, max_pending: int = 256, readiness=None, admission_check=None):
        if not repository.account_only:
            raise ValueError("应用运行服务必须使用账户范围仓库")
        if type(recovery_interval_ms) is not int or not 1 <= recovery_interval_ms <= 60000:
            raise ValueError("恢复间隔须为1到60000毫秒")
        if not 0 < retention_interval <= 86400 or type(max_pending) is not int or not 1 <= max_pending <= 10000:
            raise ValueError("保留扫描间隔或待处理容量非法")
        self.repository, self.checkpoint_db = repository, checkpoint_db
        self.supervisor = AssistantRunSupervisor(repository, runner, concurrency=concurrency,
            lease_ms=lease_ms, heartbeat_ms=heartbeat_ms) if runner is not None else None
        self.recovery_interval_ms, self.retention_interval = recovery_interval_ms, retention_interval
        self.max_pending, self._pruner = max_pending, pruner
        self._readiness, self._admission_check = readiness, admission_check
        self.stopping = asyncio.Event()
        self._started = self._closed = False
        self._recovery_task = self._retention_task = None

    def status(self):
        if self._closed:
            state = "closed"
        elif not self._started:
            state = "not_started"
        elif self._recovery_task is None or self._recovery_task.done():
            state = "unavailable"
        elif self.supervisor is None:
            state = "tools_not_connected"
        else:
            state = "running"
        return {"state": state, "can_submit": state == "running"}

    async def availability(self):
        status = self.status()
        if status["can_submit"] and self._readiness is not None:
            state = await settle_on_cancel(asyncio.to_thread(self._readiness, self.repository.db_path))
            return {"state": state, "can_submit": state == "running"}
        return status

    async def start(self):
        if self._started or self._closed:
            raise RuntimeError("运行服务不能重复启动")
        self._started = True
        self._recovery_task = asyncio.create_task(self._recover(), name="assistant-recovery")
        if self.checkpoint_db is not None:
            self._retention_task = asyncio.create_task(self._retain(), name="assistant-retention")

    async def _recover(self):
        try:
            if self.supervisor is not None:
                await self.supervisor.serve(self.stopping, interval_ms=self.recovery_interval_ms)
            else:
                # 不调用引擎时仍维护已有运行的持久时限，不让过期状态停在排队/运行中。
                while not self.stopping.is_set():
                    await settle_on_cancel(asyncio.to_thread(self.repository.expire_due))
                    try:
                        await asyncio.wait_for(self.stopping.wait(), self.recovery_interval_ms / 1000)
                    except asyncio.TimeoutError:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 后台故障显式使新提交不可用，读取/取消仍可用；不记录上游异常正文。
            LOGGER.error("助手恢复服务退出：error_type=%s", type(exc).__name__)

    async def _retain(self):
        cursor = ""
        while not self.stopping.is_set():
            try:
                if self._pruner is not None or await asyncio.to_thread(self.checkpoint_db.exists):
                    pruner = self._pruner
                    if pruner is None:
                        from ..assistant_checkpoint_retention import prune_run_checkpoints
                        pruner = prune_run_checkpoints
                    page = await pruner(self.repository.db_path, self.checkpoint_db, after=cursor, limit=50)
                    cursor = page["after"]
            except Exception as exc:
                LOGGER.error("助手检查点保留扫描失败：error_type=%s", type(exc).__name__)
            try:
                await asyncio.wait_for(self.stopping.wait(), self.retention_interval)
            except asyncio.TimeoutError:
                pass

    async def submit(self, session_id: str, request_key: str, message: str, *, access: RunAccess, language: str | None = None):
        availability = await self.availability()
        admission = await settle_on_cancel(asyncio.to_thread(self.repository.admit, session_id, request_key,
            {"message": message, **({'language': language} if language else {})}, access=access, accept_new=availability["can_submit"], max_pending=self.max_pending,
            admission_check=self._admission_check))
        snapshot = await self.inspect(admission.run["id"], access=access, limit=1)
        return {"created": admission.created, "run": snapshot["run"]}

    async def inspect(self, run_id: str, *, access: RunAccess, after: int = 0, limit: int = 100):
        return await settle_on_cancel(asyncio.to_thread(self.repository.snapshot, run_id, access=access, after=after, limit=limit))

    async def cancel(self, run_id: str, *, access: RunAccess):
        async def request():
            run = await asyncio.to_thread(self.repository.cancel, run_id, access=access)
            if run["status"] == "canceled" and self.supervisor is not None:
                self.supervisor.notify_canceled(run_id)
            return await self.inspect(run_id, access=access)
        return await settle_on_cancel(request())

    async def aclose(self):
        async def close():
            self._closed = True
            self.stopping.set()
            tasks = [task for task in (self._recovery_task, self._retention_task) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.supervisor is not None:
                await self.supervisor.aclose()
        await settle_on_cancel(close())
