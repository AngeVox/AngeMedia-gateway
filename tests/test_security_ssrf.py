"""SSRF 防护纯函数测试。"""
from __future__ import annotations

import socket
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.security import validate_public_http_url

# 固定公网 IP，用于 mock DNS 解析，避免真实网络请求
MOCK_PUBLIC_IP = "93.184.216.34"
MOCK_DNS_RESULT = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (MOCK_PUBLIC_IP, 0))]


class ValidatePublicHttpUrlTest(TestCase):
    def test_rejects_localhost(self) -> None:
        """拒绝 localhost 地址。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://localhost:8080/test")
        self.assertIn("localhost", str(ctx.exception))

    def test_rejects_localhost_no_port(self) -> None:
        """拒绝无端口的 localhost。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://localhost/test")
        self.assertIn("localhost", str(ctx.exception))

    def test_rejects_127_0_0_1(self) -> None:
        """拒绝 127.0.0.1 环回地址。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://127.0.0.1:8080/test")
        self.assertIn("内网或保留地址", str(ctx.exception))

    def test_rejects_10_0_0_1(self) -> None:
        """拒绝 10.0.0.1 内网地址。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://10.0.0.1/test")
        self.assertIn("内网或保留地址", str(ctx.exception))

    def test_rejects_192_168_1_1(self) -> None:
        """拒绝 192.168.1.1 内网地址。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://192.168.1.1/test")
        self.assertIn("内网或保留地址", str(ctx.exception))

    def test_rejects_169_254_169_254(self) -> None:
        """拒绝 169.254.169.254 链路本地地址（AWS metadata）。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://169.254.169.254/latest/meta-data/")
        self.assertIn("内网或保留地址", str(ctx.exception))

    def test_rejects_file_scheme(self) -> None:
        """拒绝 file:// 协议。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("file:///etc/passwd")
        self.assertIn("只允许 http 或 https", str(ctx.exception))

    def test_rejects_ftp_scheme(self) -> None:
        """拒绝 ftp:// 协议。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("ftp://example.com/file")
        self.assertIn("只允许 http 或 https", str(ctx.exception))

    def test_rejects_no_scheme(self) -> None:
        """拒绝无 scheme 的 URL。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("example.com/test")
        self.assertIn("只允许 http 或 https", str(ctx.exception))

    def test_rejects_empty_url(self) -> None:
        """拒绝空 URL。"""
        with self.assertRaises(ValueError):
            validate_public_http_url("")

    def test_rejects_missing_hostname(self) -> None:
        """拒绝缺少 hostname 的 URL。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://")
        self.assertIn("缺少 hostname", str(ctx.exception))

    def test_rejects_invalid_port(self) -> None:
        """拒绝无效端口。"""
        with patch("socket.getaddrinfo", side_effect=socket.error("Port out of range 0-65535")):
            with self.assertRaises(ValueError) as ctx:
                validate_public_http_url("http://example.com:99999/test")
            self.assertIn("Port out of range", str(ctx.exception))

    def test_allows_https_example_com(self) -> None:
        """允许 https://example.com/path。"""
        with patch("socket.getaddrinfo", return_value=MOCK_DNS_RESULT):
            result = validate_public_http_url("https://example.com/path?query=1")
            self.assertEqual(result, "https://example.com/path?query=1")

    def test_allows_http_example_com(self) -> None:
        """允许 http://example.com/path。"""
        with patch("socket.getaddrinfo", return_value=MOCK_DNS_RESULT):
            result = validate_public_http_url("http://example.com/test")
            self.assertEqual(result, "http://example.com/test")

    def test_allows_valid_port(self) -> None:
        """允许有效端口。"""
        with patch("socket.getaddrinfo", return_value=MOCK_DNS_RESULT):
            result = validate_public_http_url("https://example.com:443/test")
            self.assertEqual(result, "https://example.com:443/test")

    def test_strips_whitespace(self) -> None:
        """去除 URL 前后空格。"""
        with patch("socket.getaddrinfo", return_value=MOCK_DNS_RESULT):
            result = validate_public_http_url("  https://example.com/test  ")
            self.assertEqual(result, "https://example.com/test")


class EnsurePublicHttpUrlTest(TestCase):
    def test_strips_trailing_slash(self) -> None:
        """去除末尾斜杠。"""
        from angemedia_gateway.security import ensure_public_http_url
        with patch("socket.getaddrinfo", return_value=MOCK_DNS_RESULT):
            result = ensure_public_http_url("https://example.com/v1/")
            self.assertEqual(result, "https://example.com/v1")

    def test_preserves_path_without_slash(self) -> None:
        """保留无末尾斜杠的路径。"""
        from angemedia_gateway.security import ensure_public_http_url
        with patch("socket.getaddrinfo", return_value=MOCK_DNS_RESULT):
            result = ensure_public_http_url("https://example.com/v1")
            self.assertEqual(result, "https://example.com/v1")


# ── DNS 解析到私网 IP ────────────────────────────────

PRIVATE_DNS_RESULTS = {
    "127.0.0.1": [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],
    "169.254.169.254": [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))],
}


class DnsToPrivateIpTest(TestCase):
    def test_dns_resolves_to_127_rejected(self) -> None:
        """DNS 返回 127.0.0.1 时拒绝。"""
        with patch("socket.getaddrinfo", return_value=PRIVATE_DNS_RESULTS["127.0.0.1"]):
            with self.assertRaises(ValueError) as ctx:
                validate_public_http_url("https://example.com/image.png")
            self.assertIn("内网或保留地址", str(ctx.exception))

    def test_dns_resolves_to_metadata_ip_rejected(self) -> None:
        """DNS 返回 169.254.169.254 (AWS metadata) 时拒绝。"""
        with patch("socket.getaddrinfo", return_value=PRIVATE_DNS_RESULTS["169.254.169.254"]):
            with self.assertRaises(ValueError) as ctx:
                validate_public_http_url("https://example.com/image.png")
            self.assertIn("内网或保留地址", str(ctx.exception))


# ── IPv6 字面量拒绝 ──────────────────────────────────

class IPv6LiteralTest(TestCase):
    def test_rejects_ipv6_loopback(self) -> None:
        """拒绝 [::1] loopback。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://[::1]/image.png")
        self.assertIn("内网或保留地址", str(ctx.exception))

    def test_rejects_ipv6_link_local(self) -> None:
        """拒绝 [fe80::1] link-local。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://[fe80::1]/image.png")
        self.assertIn("内网或保留地址", str(ctx.exception))

    def test_rejects_ipv6_ula(self) -> None:
        """拒绝 [fc00::1] ULA/private。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://[fc00::1]/image.png")
        self.assertIn("内网或保留地址", str(ctx.exception))


# ── 0.0.0.0 字面量拒绝 ────────────────────────────────

class ZeroAddressTest(TestCase):
    def test_rejects_0_0_0_0(self) -> None:
        """拒绝 0.0.0.0 字面量。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://0.0.0.0/image.png")
        self.assertIn("内网或保留地址", str(ctx.exception))


# ── localhost 大小写变体 ───────────────────────────────

class LocalhostCaseVariantTest(TestCase):
    def test_rejects_uppercase_localhost(self) -> None:
        """拒绝 LOCALHOST 大写。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://LOCALHOST/image.png")
        self.assertIn("localhost", str(ctx.exception))

    def test_rejects_mixed_case_localdomain(self) -> None:
        """拒绝 LocalHost.localdomain。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://LocalHost.localdomain/image.png")
        self.assertIn("localhost", str(ctx.exception))


# ── userinfo@host 形式 ────────────────────────────────

class UserInfoHostTest(TestCase):
    def test_rejects_userinfo_with_private_ip(self) -> None:
        """拒绝 http://user:pass@127.0.0.1。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://user:pass@127.0.0.1/image.png")
        self.assertIn("内网或保留地址", str(ctx.exception))

    def test_rejects_userinfo_with_localhost(self) -> None:
        """拒绝 http://user:pass@localhost。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://user:pass@localhost/image.png")
        self.assertIn("localhost", str(ctx.exception))


# ── IPv4-mapped IPv6 ─────────────────────────────────

class IPv4MappedIpv6Test(TestCase):
    def test_rejects_ipv4_mapped_loopback(self) -> None:
        """拒绝 [::ffff:127.0.0.1] IPv4-mapped IPv6 loopback。"""
        with self.assertRaises(ValueError) as ctx:
            validate_public_http_url("http://[::ffff:127.0.0.1]/image.png")
        self.assertIn("内网或保留地址", str(ctx.exception))


# ── 十进制 IPv4 ──────────────────────────────────────

DECIMAL_LOOPBACK_IP = "127.0.0.1"
DECIMAL_LOOPBACK_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (DECIMAL_LOOPBACK_IP, 0))]


class DecimalIPv4Test(TestCase):
    def test_decimal_loopback_not_allowed_as_public(self) -> None:
        """十进制 2130706433 (=127.0.0.1) 解析到 loopback 时拒绝。"""
        with patch("socket.getaddrinfo", return_value=DECIMAL_LOOPBACK_DNS):
            with self.assertRaises(ValueError) as ctx:
                validate_public_http_url("http://2130706433/image.png")
            self.assertIn("内网或保留地址", str(ctx.exception))

    def test_decimal_loopback_gaierror_rejected(self) -> None:
        """系统无法解析十进制 IPv4 时，gaierror 被拒绝。"""
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
            with self.assertRaises(ValueError) as ctx:
                validate_public_http_url("http://2130706433/image.png")
            self.assertIn("解析失败", str(ctx.exception))


# ── 十六进制 IPv4 ────────────────────────────────────

HEX_LOOPBACK_IP = "127.0.0.1"
HEX_LOOPBACK_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (HEX_LOOPBACK_IP, 0))]


class HexIPv4Test(TestCase):
    def test_hex_loopback_not_allowed_as_public(self) -> None:
        """十六进制 0x7f000001 (=127.0.0.1) 解析到 loopback 时拒绝。"""
        with patch("socket.getaddrinfo", return_value=HEX_LOOPBACK_DNS):
            with self.assertRaises(ValueError) as ctx:
                validate_public_http_url("http://0x7f000001/image.png")
            self.assertIn("内网或保留地址", str(ctx.exception))

    def test_hex_loopback_gaierror_rejected(self) -> None:
        """系统无法解析十六进制 IPv4 时，gaierror 被拒绝。"""
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
            with self.assertRaises(ValueError) as ctx:
                validate_public_http_url("http://0x7f000001/image.png")
            self.assertIn("解析失败", str(ctx.exception))
