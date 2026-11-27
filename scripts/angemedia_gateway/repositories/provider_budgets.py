"""跨进程共享的本地每日提交尝试预算，与平台余额独立。"""
from __future__ import annotations

import json
import logging
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..db.connection import db_connect, db_transaction
from ..helpers import now_iso

log = logging.getLogger("angemedia-gateway")


@dataclass(frozen=True)
class LocalBudgetState:
    """配置上限限制已分配的提交尝试，不等价于成功次数或魔粒余额。"""

    day: str
    limit: int
    used: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def as_public_dict(self) -> dict:
        return {
            "scope": "gateway_provider", "unit": "submission_attempt",
            "day_basis": "server_local_day", "day": self.day,
            "limit": self.limit, "allocated": self.used, "remaining": self.remaining,
            "upstream_balance": None,
        }


def _legacy_used(path: Path, day: str, limit: int) -> int:
    """只在首次建行时导入旧计数；读取异常时保守保留当日用尽状态。"""
    try:
        if path.stat().st_size > 65536:
            raise ValueError("legacy budget state is too large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("legacy budget state is invalid")
        legacy_day = data["day"]
        if not isinstance(legacy_day, str):
            raise ValueError("legacy day must be an ISO date")
        date.fromisoformat(legacy_day)
        if legacy_day != day:
            return 0
        remaining = data["remaining"]
        if type(remaining) is not int:
            raise ValueError("legacy remaining must be an integer")
        return limit - max(0, min(limit, remaining))
    except FileNotFoundError:
        return 0
    except (OSError, ValueError, KeyError) as exc:
        log.warning("legacy local budget cannot be imported: error_type=%s", type(exc).__name__)
        return limit


def _access(provider_id: str, day: str, limit: int, legacy_file: Path, *, claim: bool) -> tuple[LocalBudgetState, bool]:
    limit = max(0, limit)
    with db_transaction(immediate=True) as conn:
        row = conn.execute("SELECT used FROM provider_local_budgets WHERE provider_id=? AND budget_day=?", (provider_id, day)).fetchone()
        if row is None:
            used = _legacy_used(legacy_file, day, limit)
            conn.execute("INSERT INTO provider_local_budgets(provider_id,budget_day,used,updated_at) VALUES(?,?,?,?)", (provider_id, day, used, now_iso()))
        else:
            used = int(row["used"])
        granted = claim and used < limit
        if granted:
            used += 1
            conn.execute("UPDATE provider_local_budgets SET used=?,updated_at=? WHERE provider_id=? AND budget_day=?", (used, now_iso(), provider_id, day))
        return LocalBudgetState(day, limit, used), granted


def read_daily_budget(provider_id: str, day: str, limit: int, legacy_file: Path) -> LocalBudgetState:
    """按渠道与日期分别记账，跨午夜乱序请求不会相互重置预算。"""
    with closing(db_connect()) as conn:
        row = conn.execute("SELECT used FROM provider_local_budgets WHERE provider_id=? AND budget_day=?", (provider_id, day)).fetchone()
    if row is not None:
        return LocalBudgetState(day, max(0, limit), int(row["used"]))
    return _access(provider_id, day, limit, legacy_file, claim=False)[0]


def claim_daily_budget(provider_id: str, day: str, limit: int, legacy_file: Path) -> bool:
    """提交前原子占用一次；失败或进程中断不退回，避免不确定提交越过预算。"""
    return _access(provider_id, day, limit, legacy_file, claim=True)[1]
