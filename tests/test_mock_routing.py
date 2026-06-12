"""Mock Provider routing + registry 集成测试。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.providers.image import build_providers
from angemedia_gateway.providers.mock import MockImageProvider
from angemedia_gateway.repositories import settings
from angemedia_gateway.routing import resolve_chain


class MockRoutingTest(TestCase):
    def test_resolve_chain_mock_returns_single_target(self) -> None:
        """resolve_chain("mock") 只返回 1 个 target。"""
        chain = resolve_chain("mock")
        self.assertEqual(len(chain), 1)

    def test_resolve_chain_mock_provider_is_mock(self) -> None:
        """resolve_chain("mock") 返回的 target.provider 为 "mock"。"""
        chain = resolve_chain("mock")
        self.assertEqual(chain[0].provider, "mock")

    def test_resolve_chain_mock_model_is_mock_model(self) -> None:
        """resolve_chain("mock") 返回的 target.model 为 "mock-model"。"""
        chain = resolve_chain("mock")
        self.assertEqual(chain[0].model, "mock-model")

    def test_resolve_chain_mock_no_pollinations_fallback(self) -> None:
        """resolve_chain("mock") 返回链中不包含 "pollinations"。"""
        chain = resolve_chain("mock")
        providers = [target.provider for target in chain]
        self.assertNotIn("pollinations", providers)

    def test_build_providers_contains_mock(self) -> None:
        """build_providers() 包含 "mock" 键。"""
        providers = build_providers()
        self.assertIn("mock", providers)

    def test_build_providers_mock_is_correct_class(self) -> None:
        """build_providers()["mock"] 是 MockImageProvider 实例。"""
        providers = build_providers()
        self.assertIsInstance(providers["mock"], MockImageProvider)

    def test_default_chain_has_no_pollinations_even_when_enabled(self) -> None:
        """默认链不再包含 Pollinations。"""
        with patch("angemedia_gateway.routing.builtin_provider_enabled", return_value=True):
            chain = resolve_chain(None)
        providers = [target.provider for target in chain]
        self.assertNotIn("pollinations", providers)

    def test_builtin_alias_has_no_pollinations_fallback(self) -> None:
        """显式内置别名不再追加 Pollinations 兜底。"""
        with patch("angemedia_gateway.routing.builtin_provider_enabled", return_value=True):
            chain = resolve_chain("kolors")
        self.assertEqual([target.provider for target in chain], ["siliconflow"])

    def test_raw_model_has_no_pollinations_fallback(self) -> None:
        """原始模型名不再追加 Pollinations 兜底。"""
        with patch("angemedia_gateway.routing.builtin_provider_enabled", return_value=True):
            chain = resolve_chain("Qwen/Qwen-Image-2512")
        self.assertEqual([target.provider for target in chain], ["modelscope"])

    def test_explicit_pollinations_alias_remains_available_when_enabled(self) -> None:
        """Pollinations 仅作为显式启用的 experimental provider 保留。"""
        with patch("angemedia_gateway.routing.builtin_provider_enabled", return_value=True):
            chain = resolve_chain("pollinations")
        self.assertEqual([target.provider for target in chain], ["pollinations"])

    def test_pollinations_builtin_setting_defaults_disabled(self) -> None:
        """没有 DB/env 显式设置时，Pollinations 内置开关默认关闭。"""
        with patch("angemedia_gateway.repositories.settings.get_config", return_value="false") as mock_get:
            enabled = settings.builtin_provider_enabled("pollinations")
        self.assertFalse(enabled)
        mock_get.assert_called_once_with("BUILTIN_PROVIDER_POLLINATIONS_ENABLED", "false")
