"""管理有界异步执行、租约及恢复；不实现模型/工具循环，也不依赖SDK。"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

from ..assistant_async import settle_on_cancel
from ..assistant_execution import RunExecution, RunResult
from ..assistant_runtime_contract import (
    TERMINAL_RUN_STATUSES, RunBudgetExceeded, RunBusy, RunClosed, RunLeaseLost, RunNotFound,
)
from ..repositories.assistant_runtime import AssistantRunRepository

LOGGER = logging.getLogger(__name__)
Runner = Callable[[RunExecution], Awaitable[RunResult]]


class AssistantRunSupervisor:
    def __init__(self, repository: AssistantRunRepository, runner: Runner, *, concurrency: int = 4,
                 lease_ms: int = 30000, heartbeat_ms: int = 1000):
        if type(concurrency) is not int or not 1 <= concurrency <= 32:
            raise ValueError("并发数必须是1到32的整数")
        if (type(lease_ms) is not int or not 1 <= lease_ms <= 300000
            or type(heartbeat_ms) is not int or not 1 <= heartbeat_ms <= lease_ms // 3):
            raise ValueError("心跳须为正整数且不超过租约的三分之一，租约最多300秒")
        self.repository, self.runner = repository, runner
        self.concurrency, self.lease_ms, self.heartbeat_ms = concurrency, lease_ms, heartbeat_ms
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_signals: dict[str, asyncio.Event] = {}
        self._closed = False
        self._serving = False

    @property
    def active_count(self) -> int:
        return sum(not task.done() for task in self._tasks.values())

    async def _db(self, function, *args, **kwargs):
        return await settle_on_cancel(asyncio.to_thread(function, *args, **kwargs))

    def _schedule(self, run_id: str):
        if self._closed:
            raise RuntimeError("运行控制器已关闭")
        existing = self._tasks.get(run_id)
        if existing is not None:
            return existing
        if self.active_count >= self.concurrency:
            raise RunBusy("本进程运行容量已满，请保留持久队列等待")
        signal = asyncio.Event()
        task = asyncio.create_task(self._execute(run_id, signal))
        self._tasks[run_id] = task
        self._cancel_signals[run_id] = signal

        def completed(done):
            if self._tasks.get(run_id) is done:
                self._tasks.pop(run_id)
                self._cancel_signals.pop(run_id, None)
            if not done.cancelled() and done.exception() is not None:
                # 后台任务也取回异常；不把上游错误体或凭据写入日志。
                LOGGER.error("助手运行控制异常：run_id=%s", run_id)

        task.add_done_callback(completed)
        return task

    async def execute(self, run_id: str):
        """尝试执行一个持久运行；调用者断开只取消等待，不取消后台执行。"""
        if self._closed:
            raise RuntimeError("运行控制器已关闭")
        run = await self._db(self.repository.get, run_id)
        if run["status"] in TERMINAL_RUN_STATUSES:
            return run
        return await asyncio.shield(self._schedule(run_id))

    async def cancel(self, run_id: str):
        async def request():
            run = await asyncio.to_thread(self.repository.cancel, run_id)
            if run["status"] == "canceled":
                self.notify_canceled(run_id)
            return run
        return await settle_on_cancel(request())

    def notify_canceled(self, run_id: str) -> None:
        """授权服务已提交取消后唤醒本地执行；其他进程仍由租约心跳发现。"""
        signal = self._cancel_signals.get(run_id)
        if signal is not None:
            signal.set()

    async def _latest(self, run_id):
        try:
            return await self._db(self.repository.get, run_id)
        except RunNotFound:
            return None

    async def _monitor(self, context, deadline, signal):
        loop = asyncio.get_running_loop()
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return "deadline"
            try:
                await asyncio.wait_for(signal.wait(), min(self.heartbeat_ms / 1000, remaining))
                return "lease_lost"
            except asyncio.TimeoutError:
                pass
            if loop.time() >= deadline:
                return "deadline"
            try:
                await self._db(self.repository.renew, context.lease, lease_ms=self.lease_ms)
            except (RunLeaseLost, RunNotFound, ValueError):
                return "lease_lost"

    async def _execute(self, run_id, signal):
        lease = context = worker = monitor = None
        try:
            run = await self._db(self.repository.get, run_id)
            if run["status"] in TERMINAL_RUN_STATUSES:
                return run

            async def acquire():
                nonlocal lease
                # 赋值发生在受保护任务内，取得租约期间的取消不会丢失清理凭据。
                lease = await asyncio.to_thread(self.repository.claim, run_id, lease_ms=self.lease_ms)

            try:
                await settle_on_cancel(acquire())
            except (RunBusy, RunClosed):
                await self._db(self.repository.expire_due, session_id=run["session_id"])
                return await self._latest(run_id)
            run = await self._db(self.repository.get, run_id)
            await self._db(self.repository.renew, lease, lease_ms=self.lease_ms)
            deadline = asyncio.get_running_loop().time() + max(0, (run["deadline_ms"] - self.repository.clock()) / 1000)
            context = RunExecution(lease, json.loads(run["input_json"]), run["deadline_ms"], self.repository)
            monitor = asyncio.create_task(self._monitor(context, deadline, signal))
            worker = asyncio.create_task(self.runner(context))
            done, _ = await asyncio.wait((monitor, worker), return_when=asyncio.FIRST_COMPLETED)
            if monitor in done:
                reason = monitor.result()
                context._stopped.set()
                if reason == "deadline":
                    await self._db(self.repository.timeout_owned, lease)
                return await self._latest(run_id)
            try:
                result = worker.result()
            except (RunLeaseLost, RunBudgetExceeded):
                return await self._latest(run_id)
            except asyncio.CancelledError:
                result = RunResult("failed", {"error_code": "engine_canceled"})
            except Exception:
                result = RunResult("failed", {"error_code": "engine_error"})
            context._stopped.set()
            if not isinstance(result, RunResult):
                result = RunResult("failed", {"error_code": "invalid_engine_result"})
            try:
                return await self._db(self.repository.finish, lease, status=result.status, output=result.output, answer=result.answer)
            except (TypeError, ValueError):
                return await self._db(self.repository.finish, lease, status="failed", output={"error_code": "invalid_engine_result"})
        except (RunLeaseLost, RunNotFound):
            # 接管、归档、预算或外部取消已改变持久状态，旧执行不能覆盖它。
            return await self._latest(run_id)
        finally:
            async def cleanup():
                if context is not None:
                    context._stopped.set()
                children = [task for task in (worker, monitor) if task is not None]
                for task in children:
                    if not task.done():
                        task.cancel()
                if children:
                    # 等待合作式引擎及数据库收尾；未退出的执行继续占用本地容量。
                    await asyncio.gather(*children, return_exceptions=True)
                if lease is not None:
                    with suppress(RunLeaseLost, RunNotFound, ValueError):
                        await asyncio.to_thread(self.repository.pause, lease)
            await settle_on_cancel(cleanup())

    async def recover_once(self, *, limit: int = 100) -> int:
        """清理到期队首，发现候选；真正执行仍需原子claim。"""
        if self._closed:
            raise RuntimeError("运行控制器已关闭")
        await self._db(self.repository.expire_due, limit=limit)
        capacity = self.concurrency - self.active_count
        if capacity <= 0:
            return 0
        # 失租后尚在收尾的本地任务也可能出现在ready中，不能遮住其他会话的队首。
        candidates = await self._db(self.repository.ready, limit=min(500, limit + len(self._tasks)))
        scheduled = 0
        for run in candidates:
            if run["id"] not in self._tasks and self.active_count < self.concurrency:
                self._schedule(run["id"])
                scheduled += 1
        return scheduled

    async def serve(self, stop: asyncio.Event, *, interval_ms: int = 1000):
        if type(interval_ms) is not int or not 1 <= interval_ms <= 60000:
            raise ValueError("恢复扫描间隔必须是1到60000毫秒的整数")
        if self._serving or self._closed:
            raise RuntimeError("控制器已启动或关闭")
        self._serving = True
        try:
            while not stop.is_set():
                await self.recover_once()
                try:
                    await asyncio.wait_for(stop.wait(), interval_ms / 1000)
                except asyncio.TimeoutError:
                    pass
        finally:
            self._serving = False
            await self.aclose()

    async def aclose(self):
        self._closed = True
        tasks = list(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await settle_on_cancel(asyncio.gather(*tasks, return_exceptions=True))

    async def __aenter__(self):
        if self._closed:
            raise RuntimeError("运行控制器已关闭")
        return self

    async def __aexit__(self, *exc):
        await self.aclose()
