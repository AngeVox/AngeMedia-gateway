"""密钥脱敏/掩码纯函数测试。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.security import redact_secret_text
from angemedia_gateway.job_sanitizer import sanitize_error_text, sanitize_job_value
from angemedia_gateway.state import mask_secret


class RedactSecretTextTest(TestCase):
    def test_redacts_sk_key(self) -> None:
        """脱敏 sk- 开头的 API Key。"""
        result = redact_secret_text("sk-1234567890abcdef")
        self.assertEqual(result, "***REDACTED***")
        self.assertNotIn("1234567890abcdef", result)

    def test_redacts_am_key(self) -> None:
        """脱敏 am- 开头的 Gateway Key。"""
        result = redact_secret_text("am-abcdef1234567890")
        self.assertEqual(result, "***REDACTED***")
        self.assertNotIn("abcdef1234567890", result)

    def test_redacts_av_key(self) -> None:
        """脱敏 av- 开头的密钥。"""
        result = redact_secret_text("av-1234567890abcdef")
        self.assertEqual(result, "***REDACTED***")
        self.assertNotIn("1234567890abcdef", result)

    def test_redacts_bearer_token(self) -> None:
        """脱敏 Bearer token。"""
        result = redact_secret_text("Authorization: Bearer sk-1234567890abcdef")
        self.assertEqual(result, "Authorization: Bearer ***REDACTED***")
        self.assertNotIn("sk-1234567890abcdef", result)

    def test_redacts_bearer_case_insensitive(self) -> None:
        """Bearer 脱敏不区分大小写。"""
        result = redact_secret_text("bearer sk-1234567890abcdef")
        self.assertEqual(result, "bearer ***REDACTED***")

    def test_preserves_short_sk_key(self) -> None:
        """保留短于 12 字符的 sk- 密钥（不匹配模式）。"""
        result = redact_secret_text("sk-short")
        self.assertEqual(result, "sk-short")

    def test_preserves_short_am_key(self) -> None:
        """保留短于 12 字符的 am- 密钥（不匹配模式）。"""
        result = redact_secret_text("am-short")
        self.assertEqual(result, "am-short")

    def test_preserves_normal_text(self) -> None:
        """保留普通文本不变。"""
        text = "This is a normal error message without secrets."
        result = redact_secret_text(text)
        self.assertEqual(result, text)

    def test_redacts_multiple_secrets(self) -> None:
        """脱敏文本中的多个密钥。"""
        text = "Key1: sk-1234567890abcdef, Key2: am-abcdef1234567890"
        result = redact_secret_text(text)
        self.assertNotIn("1234567890abcdef", result)
        self.assertNotIn("abcdef1234567890", result)
        self.assertIn("***REDACTED***", result)

    def test_preserves_non_secret_text_around_key(self) -> None:
        """保留密钥前后的普通文本。"""
        text = "Error connecting to sk-1234567890abcdef failed"
        result = redact_secret_text(text)
        self.assertIn("Error connecting to", result)
        self.assertIn("failed", result)
        self.assertNotIn("sk-1234567890abcdef", result)


class MaskSecretTest(TestCase):
    def test_masks_long_key(self) -> None:
        """掩码长密钥（>8 字符）：前4 + 星号 + 后4。"""
        result = mask_secret("sk-1234567890abcdef")
        # 19 chars: first 4 + (19-8)=11 asterisks + last 4
        self.assertEqual(result, "sk-1***********cdef")
        self.assertNotIn("1234567890", result)

    def test_masks_am_key(self) -> None:
        """掩码 am- 开头的密钥。"""
        result = mask_secret("am-abcdef1234567890")
        # 19 chars: first 4 + (19-8)=11 asterisks + last 4
        self.assertEqual(result, "am-a***********7890")

    def test_masks_av_key(self) -> None:
        """掩码 av- 开头的密钥。"""
        result = mask_secret("av-1234567890abcdef")
        # 19 chars: first 4 + (19-8)=11 asterisks + last 4
        self.assertEqual(result, "av-1***********cdef")

    def test_masks_exactly_8_chars(self) -> None:
        """掩码正好 8 字符的密钥。"""
        result = mask_secret("12345678")
        self.assertEqual(result, "********")

    def test_masks_short_key(self) -> None:
        """掩码短于 8 字符的密钥：全部星号。"""
        result = mask_secret("short")
        self.assertEqual(result, "*****")

    def test_masks_empty_string(self) -> None:
        """掩码空字符串：返回空字符串。"""
        result = mask_secret("")
        self.assertEqual(result, "")

    def test_masks_single_char(self) -> None:
        """掩码单字符：返回单个星号。"""
        result = mask_secret("a")
        self.assertEqual(result, "*")

    def test_masks_7_chars(self) -> None:
        """掩码 7 字符：全部星号。"""
        result = mask_secret("1234567")
        self.assertEqual(result, "*******")

    def test_masks_9_chars(self) -> None:
        """掩码 9 字符：前4 + 星号 + 后4。"""
        result = mask_secret("123456789")
        self.assertEqual(result, "1234*6789")


class JobSanitizerDataUrlTest(TestCase):
    def test_redacts_data_url_payload(self) -> None:
        payload = "data:image/png;base64," + ("A" * 128)
        result = sanitize_error_text(f"preview={payload}", limit=1000)
        self.assertIn("data:image/png;base64,<redacted-data-url>", result or "")
        self.assertNotIn("A" * 64, result or "")

    def test_long_data_url_is_bounded_and_fast(self) -> None:
        payload = "data:image/png;base64," + ("A" * (256 * 1024))
        result = sanitize_error_text(payload, limit=200000)
        self.assertLess(len(result or ""), 70 * 1024)
        self.assertIn("<redacted-data-url>", result or "")
        self.assertNotIn("A" * 1024, result or "")

    def test_redacts_multiple_data_urls(self) -> None:
        text = (
            "first data:image/png;base64,"
            + ("A" * 32)
            + " second data:image/jpeg;base64,"
            + ("B" * 32)
        )
        result = sanitize_error_text(text, limit=1000) or ""
        self.assertEqual(result.count("<redacted-data-url>"), 2)
        self.assertNotIn("A" * 16, result)
        self.assertNotIn("B" * 16, result)

    def test_redacts_incomplete_data_url(self) -> None:
        result = sanitize_error_text("broken data:image/png;base64" + ("A" * 256), limit=1000) or ""
        self.assertIn("[REDACTED_DATA_URL]", result)
        self.assertNotIn("A" * 64, result)

    def test_preserves_normal_text(self) -> None:
        text = "ordinary provider message without embedded media"
        self.assertEqual(sanitize_error_text(text), text)

    def test_signed_url_and_secret_redaction_do_not_regress(self) -> None:
        result = sanitize_job_value({
            "message": "Bearer sk-1234567890abcdef https://cdn.example/x.png?token=signed-secret",
            "signed_url": "https://cdn.example/y.png?token=must-not-leak",
        })
        rendered = str(result)
        self.assertIn("[REDACTED_SIGNED_URL]", rendered)
        self.assertIn("[REDACTED]", rendered)
        self.assertNotIn("sk-1234567890abcdef", rendered)
        self.assertNotIn("signed-secret", rendered)
        self.assertNotIn("must-not-leak", rendered)

    def test_whole_string_is_bounded_before_redaction(self) -> None:
        text = (
            "Bearer sk-1234567890abcdef "
            + ("x" * (70 * 1024))
            + " data:image/png;base64,"
            + ("A" * 2048)
        )
        result = sanitize_error_text(text, limit=200000) or ""
        self.assertLess(len(result), 70 * 1024)
        self.assertIn("Bearer ***REDACTED***", result)
        self.assertIn("[TRUNCATED]", result)
        self.assertNotIn("sk-1234567890abcdef", result)
        self.assertNotIn("A" * 1024, result)
