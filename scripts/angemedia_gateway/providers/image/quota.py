"""ModelScope本地保护预算的异步入口；持久化统一交给repository。"""
from __future__ import annotations

import asyncio
from datetime import date

from ... import config as C
from ...repositories.provider_budgets import LocalBudgetState, claim_daily_budget, read_daily_budget


class LocalQuota:
    """沿用既有类型名，预算单位明确为提交尝试；不缓存进程内余额。"""

    async def try_acquire(self) -> bool:
        return await asyncio.to_thread(
            claim_daily_budget, "modelscope", date.today().isoformat(),
            C.MODELSCOPE_DAILY_LIMIT, C.QUOTA_FILE,
        )

    def snapshot(self) -> LocalBudgetState:
        return read_daily_budget("modelscope", date.today().isoformat(), C.MODELSCOPE_DAILY_LIMIT, C.QUOTA_FILE)


quota = LocalQuota()
