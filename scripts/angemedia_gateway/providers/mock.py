"""Mock Provider 实现，用于测试和离线开发。"""
from __future__ import annotations

from typing import Any

from ..schemas import ImageRequest
from .base import RouteTarget


class MockImageProvider:
    """Mock 图片 Provider，返回固定测试图片，不发起任何外部请求。"""

    name = "mock"

    async def generate(self, req: ImageRequest, target: RouteTarget) -> dict[str, Any]:
        """返回 1x1 像素 PNG（base64 编码）。

        符合 OpenAI Images API 响应格式：
        {"data": [{"b64_json": "..."}]}
        """
        # 1x1 像素白色 PNG（base64 编码）
        fixed_b64_png = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        return {"data": [{"b64_json": fixed_b64_png}]}

    def health(self) -> str:
        """Mock Provider 始终可用，无需外部配置。"""
        return "configured"
