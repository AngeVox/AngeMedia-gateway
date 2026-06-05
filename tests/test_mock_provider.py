"""Mock Provider 单元测试。"""
from __future__ import annotations

import base64
import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.providers.mock import MockImageProvider
from angemedia_gateway.schemas import ImageRequest


class MockProviderTest(TestCase):
    def setUp(self) -> None:
        self.provider = MockImageProvider()
        self.target = RouteTarget(provider="mock", model="mock-model")
        self.req = ImageRequest(prompt="test prompt")

    def test_generate_returns_valid_structure(self) -> None:
        """测试 generate() 返回符合 OpenAI Images API 的结构。"""
        import asyncio
        result = asyncio.run(self.provider.generate(self.req, self.target))

        self.assertIn("data", result)
        self.assertIsInstance(result["data"], list)
        self.assertEqual(len(result["data"]), 1)

        item = result["data"][0]
        self.assertIsInstance(item, dict)

    def test_generate_returns_b64_json(self) -> None:
        """测试 generate() 返回 b64_json 而非 url。"""
        import asyncio
        result = asyncio.run(self.provider.generate(self.req, self.target))

        item = result["data"][0]
        self.assertIn("b64_json", item)
        self.assertNotIn("url", item)

    def test_b64_json_is_valid_base64(self) -> None:
        """测试 b64_json 可以被正确解码为有效的 PNG。"""
        import asyncio
        result = asyncio.run(self.provider.generate(self.req, self.target))

        b64_data = result["data"][0]["b64_json"]
        self.assertIsInstance(b64_data, str)
        self.assertTrue(len(b64_data) > 0)

        # 验证 base64 解码
        try:
            decoded = base64.b64decode(b64_data)
        except Exception as e:
            self.fail(f"base64 解码失败: {e}")

        # 验证 PNG 签名（前 8 字节）
        png_signature = b'\x89PNG\r\n\x1a\n'
        self.assertTrue(decoded[:8] == png_signature, "解码后的数据不是有效的 PNG 文件")

    def test_health_returns_configured(self) -> None:
        """测试 health() 返回 'configured'，表示无需外部配置。"""
        result = self.provider.health()
        self.assertEqual(result, "configured")
        self.assertIsInstance(result, str)

    def test_provider_name_is_mock(self) -> None:
        """测试 Provider name 为 'mock'。"""
        self.assertEqual(self.provider.name, "mock")

    def test_no_external_requests(self) -> None:
        """验证 generate() 不发起任何外部网络请求。

        通过检查返回结果不包含 http/https URL 来间接验证。
        """
        import asyncio
        result = asyncio.run(self.provider.generate(self.req, self.target))

        item = result["data"][0]
        # 不应包含 url 字段（避免触发 localize_image_result 下载）
        self.assertNotIn("url", item)

        # b64_json 不应包含 http:// 或 https://
        b64_data = item.get("b64_json", "")
        self.assertNotIn("http://", b64_data)
        self.assertNotIn("https://", b64_data)
