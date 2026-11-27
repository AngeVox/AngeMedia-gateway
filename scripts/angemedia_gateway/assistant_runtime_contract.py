"""助手运行的状态、租约凭据及拒绝分类，不依赖模型或HTTP。"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any

ACTIVE_RUN_STATUSES = ("queued", "running", "paused")
TERMINAL_RUN_STATUSES = ("succeeded", "failed", "refused", "canceled", "timed_out", "budget_exceeded")


class AssistantRunError(RuntimeError):
    pass


class RunNotFound(AssistantRunError):
    pass


class RunClosed(AssistantRunError):
    pass


class RunBusy(AssistantRunError):
    pass


class RunLeaseLost(AssistantRunError):
    pass


class RunRequestConflict(AssistantRunError):
    pass


class RunBudgetExceeded(AssistantRunError):
    pass


@dataclass(frozen=True)
class RunLease:
    run_id: str
    session_id: str
    fence: int
    owner_token: str = field(repr=False)


@dataclass(frozen=True)
class RunAdmission:
    run: dict[str, Any]
    created: bool


def require_run_owner(run: Mapping[str, Any] | None, slot: Mapping[str, Any] | None,
                      lease: RunLease) -> None:
    """核对当前执行代际；仅到期收尾可不再要求时限有效，调用者必须持有写锁。"""
    if run is None:
        raise RunNotFound("运行不存在或不属于运行内核")
    if (run["session_id"] != lease.session_id or run["status"] != "running"
        or slot is None
        or slot["run_id"] != lease.run_id or slot["owner_token"] != lease.owner_token
        or slot["fence"] != lease.fence):
        raise RunLeaseLost("运行租约已失效")


def require_owned_run(run: Mapping[str, Any] | None, slot: Mapping[str, Any] | None,
                      lease: RunLease, now_ms: int) -> None:
    """同步仓库与异步检查点共用同一租约判定；调用者必须持有运行库写锁。"""
    require_run_owner(run, slot, lease)
    if now_ms >= run["deadline_ms"] or slot["expires_ms"] <= now_ms:
        raise RunLeaseLost("运行租约已失效")
