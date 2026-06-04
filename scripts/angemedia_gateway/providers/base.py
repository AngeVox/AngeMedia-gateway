"""Provider 基础类型。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..schemas import ImageRequest


class RateLimited(RuntimeError):
    """后端限流或额度耗尽。"""


class BackendUnavailable(RuntimeError):
    """后端不可用。"""


@dataclass(frozen=True)
class RouteTarget:
    provider: str
    model: str


class ProviderBase(Protocol):
    name: str

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict:
        ...

    def health(self):
        ...
