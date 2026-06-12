"""ProviderError 结构化异常地基兼容测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.providers.errors import (  # noqa: E402
    BackendUnavailable,
    ProviderAuthError,
    ProviderError,
    ProviderProtocolError,
    ProviderRateLimited,
    ProviderTaskFailed,
    ProviderTimeout,
    ProviderUnavailable,
    ProviderValidationError,
    RateLimited,
)


class ProviderErrorCompatTest(unittest.TestCase):
    """证明旧接口完全兼容，新结构化字段可用。"""

    # ── 旧接口兼容 ──────────────────────────────────────────

    def test_backend_unavailable_still_creatable(self) -> None:
        exc = BackendUnavailable("service down")
        self.assertEqual(str(exc), "service down")

    def test_rate_limited_still_creatable(self) -> None:
        exc = RateLimited("rate limited")
        self.assertEqual(str(exc), "rate limited")

    def test_backend_unavailable_is_provider_error(self) -> None:
        exc = BackendUnavailable("x")
        self.assertIsInstance(exc, ProviderError)
        self.assertIsInstance(exc, ProviderUnavailable)

    def test_rate_limited_is_provider_error(self) -> None:
        exc = RateLimited("x")
        self.assertIsInstance(exc, ProviderError)
        self.assertIsInstance(exc, ProviderRateLimited)

    def test_provider_error_is_runtime_error(self) -> None:
        self.assertIsInstance(ProviderError("x"), RuntimeError)

    def test_backend_unavailable_is_runtime_error(self) -> None:
        self.assertIsInstance(BackendUnavailable("x"), RuntimeError)

    def test_rate_limited_is_runtime_error(self) -> None:
        self.assertIsInstance(RateLimited("x"), RuntimeError)

    def test_str_returns_safe_message_only(self) -> None:
        self.assertEqual(str(BackendUnavailable("safe message")), "safe message")
        self.assertEqual(str(RateLimited("rate limited")), "rate limited")

    # ── 旧 except 兼容 ─────────────────────────────────────

    def test_except_backend_unavailable_still_catches(self) -> None:
        with self.assertRaises(BackendUnavailable):
            raise BackendUnavailable("x")

    def test_except_rate_limited_still_catches(self) -> None:
        with self.assertRaises(RateLimited):
            raise RateLimited("x")

    def test_except_provider_error_catches_old_exceptions(self) -> None:
        with self.assertRaises(ProviderError):
            raise BackendUnavailable("x")
        with self.assertRaises(ProviderError):
            raise RateLimited("x")

    def test_structured_backend_errors_are_caught_by_backend_unavailable(self) -> None:
        for exc in (
            ProviderAuthError("x"),
            ProviderTimeout("x"),
            ProviderValidationError("x"),
            ProviderProtocolError("x"),
            ProviderTaskFailed("x"),
        ):
            with self.subTest(exc=type(exc).__name__):
                self.assertIsInstance(exc, BackendUnavailable)

    # ── 结构化字段 ──────────────────────────────────────────

    def test_rate_limited_defaults(self) -> None:
        exc = RateLimited("x")
        self.assertTrue(exc.retryable)
        self.assertEqual(exc.error_category, "rate_limit")
        self.assertEqual(exc.status_code, 429)

    def test_backend_unavailable_defaults(self) -> None:
        exc = BackendUnavailable("x")
        self.assertFalse(exc.retryable)
        self.assertEqual(exc.error_category, "upstream")
        self.assertIsNone(exc.status_code)

    def test_provider_auth_error_fields(self) -> None:
        exc = ProviderAuthError("bad key", status_code=401)
        self.assertEqual(str(exc), "bad key")
        self.assertEqual(exc.status_code, 401)
        self.assertFalse(exc.retryable)
        self.assertEqual(exc.error_category, "auth")

    def test_provider_timeout_fields(self) -> None:
        exc = ProviderTimeout("timeout")
        self.assertTrue(exc.retryable)
        self.assertEqual(exc.error_category, "timeout")
        self.assertIsNone(exc.status_code)

    def test_provider_validation_error_fields(self) -> None:
        exc = ProviderValidationError("invalid size")
        self.assertFalse(exc.retryable)
        self.assertEqual(exc.error_category, "validation")
        self.assertEqual(exc.status_code, 400)

    def test_provider_protocol_error_fields(self) -> None:
        exc = ProviderProtocolError("invalid json", status_code=200)
        self.assertFalse(exc.retryable)
        self.assertEqual(exc.error_category, "protocol")
        self.assertEqual(exc.status_code, 200)

    def test_provider_task_failed_fields(self) -> None:
        exc = ProviderTaskFailed("task failed")
        self.assertFalse(exc.retryable)
        self.assertEqual(exc.error_category, "task_failed")

    def test_provider_unavailable_with_status_code(self) -> None:
        exc = ProviderUnavailable("server error", status_code=503, retryable=True)
        self.assertEqual(exc.status_code, 503)
        self.assertTrue(exc.retryable)
        self.assertEqual(exc.error_category, "upstream")

    # ── 不泄露 raw body / repr ─────────────────────────────

    def test_str_never_includes_repr_or_traceback(self) -> None:
        exc = BackendUnavailable("connection refused")
        text = str(exc)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("RuntimeError", text)
        self.assertNotIn("object at 0x", text)
        self.assertEqual(text, "connection refused")

    # ── Exception.args 兼容 ─────────────────────────────────

    def test_provider_error_args(self) -> None:
        self.assertEqual(ProviderError("x").args, ("x",))

    def test_backend_unavailable_args(self) -> None:
        self.assertEqual(BackendUnavailable("x").args, ("x",))

    def test_rate_limited_args(self) -> None:
        self.assertEqual(RateLimited("x").args, ("x",))

    # ── 参数传递完整性（方案 A）─────────────────────────────

    def test_backend_unavailable_override_status_and_retryable(self) -> None:
        exc = BackendUnavailable("x", status_code=503, retryable=True)
        self.assertEqual(exc.status_code, 503)
        self.assertTrue(exc.retryable)

    def test_backend_unavailable_override_error_category(self) -> None:
        exc = BackendUnavailable("x", error_category="network")
        self.assertEqual(exc.error_category, "network")

    def test_rate_limited_override_retryable(self) -> None:
        exc = RateLimited("x", retryable=False)
        self.assertFalse(exc.retryable)

    def test_rate_limited_override_all_fields(self) -> None:
        exc = RateLimited("x", status_code=403, retryable=False, error_category="quota")
        self.assertEqual(exc.status_code, 403)
        self.assertFalse(exc.retryable)
        self.assertEqual(exc.error_category, "quota")


if __name__ == "__main__":
    unittest.main()
