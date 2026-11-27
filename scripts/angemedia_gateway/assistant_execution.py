"""引擎与运行控制之间的窄合同；持久状态只由运行仓库维护。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .assistant_async import settle_on_cancel
from .assistant_runtime_contract import RunLease, RunLeaseLost
from .repositories.assistant_runtime import AssistantRunRepository


@dataclass(frozen=True)
class RunResult:
    status: str = "succeeded"
    output: Any = None
    answer: str | None = None

    def __post_init__(self):
        if self.status not in {"succeeded", "failed", "refused"}:
            raise ValueError("引擎完成状态不受支持")
        if self.answer is not None and not isinstance(self.answer, str):
            raise ValueError("引擎答复必须是文本或空值")


@dataclass(frozen=True)
class RunExecution:
    lease: RunLease
    input: Any
    deadline_ms: int
    repository: AssistantRunRepository = field(repr=False)
    _stopped: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    async def _call(self, function, *args, **kwargs):
        self.raise_if_stopped()
        result = await settle_on_cancel(asyncio.to_thread(function, self.lease, *args, **kwargs))
        self.raise_if_stopped()
        return result

    def raise_if_stopped(self) -> None:
        if self._stopped.is_set():
            raise RunLeaseLost("当前执行已停止")

    async def reserve_call(self, kind: str) -> None:
        """模型或工具每次外部调用前占位；取消/失租后禁止发起下一次调用。"""
        await self._call(self.repository.reserve_call, kind)

    async def publish_message(self, role: str, content: str):
        return await self._call(self.repository.publish_message, role, content)

    async def append_event(self, event_type: str, payload: Any = None):
        await self._call(self.repository.append_event, event_type, payload)
