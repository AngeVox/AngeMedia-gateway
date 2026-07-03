"""路径穿越防护纯函数测试。"""
from __future__ import annotations

import ctypes
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import TestCase

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.state import safe_unlink_under, validate_provider_id


class SafeUnlinkUnderTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test-security-path-")
        self.base_dir = Path(self.temp_dir)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_rejects_outside_path(self) -> None:
        """拒绝删除 base_dir 外的文件。"""
        with self.assertRaises(HTTPException) as ctx:
            safe_unlink_under("/etc/passwd", self.base_dir)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("拒绝删除目录外文件", ctx.exception.detail)

    def test_rejects_relative_path_traversal(self) -> None:
        """拒绝相对路径穿越。"""
        with self.assertRaises(HTTPException) as ctx:
            safe_unlink_under("../../etc/passwd", self.base_dir)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_absolute_path_outside(self) -> None:
        """拒绝绝对路径在 base_dir 外。"""
        with self.assertRaises(HTTPException) as ctx:
            safe_unlink_under("/tmp/malicious.txt", self.base_dir)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_returns_false_for_empty_path(self) -> None:
        """空路径返回 False。"""
        result = safe_unlink_under("", self.base_dir)
        self.assertFalse(result)

    def test_returns_false_for_nonexistent_file(self) -> None:
        """不存在的文件返回 False。"""
        test_file = self.base_dir / "nonexistent.txt"
        result = safe_unlink_under(str(test_file), self.base_dir)
        self.assertFalse(result)

    def test_returns_false_for_directory(self) -> None:
        """目录返回 False（不删除目录）。"""
        test_dir = self.base_dir / "subdir"
        test_dir.mkdir()
        result = safe_unlink_under(str(test_dir), self.base_dir)
        self.assertFalse(result)
        self.assertTrue(test_dir.exists())

    def test_deletes_file_inside_base_dir(self) -> None:
        """删除 base_dir 内的文件返回 True。"""
        test_file = self.base_dir / "test.txt"
        test_file.write_text("test content")
        result = safe_unlink_under(str(test_file), self.base_dir)
        self.assertTrue(result)
        self.assertFalse(test_file.exists())

    @unittest.skipIf(
        sys.platform == "win32" and not ctypes.windll.shell32.IsUserAnAdmin(),  # type: ignore[attr-defined]
        "Windows requires admin privileges to create symlinks",
    )
    def test_rejects_symlink_outside(self) -> None:
        """符号链接指向 base_dir 外会被拒绝。"""
        with tempfile.TemporaryDirectory(prefix="external-security-path-") as external_dir:
            external_file = Path(external_dir) / "external.txt"
            external_file.write_text("external content")
            symlink = self.base_dir / "symlink.txt"
            symlink.symlink_to(external_file)
            with self.assertRaises(HTTPException):
                safe_unlink_under(str(symlink), self.base_dir)
            symlink.unlink(missing_ok=True)

    def test_handles_path_with_dots(self) -> None:
        """处理包含 . 的路径。"""
        test_file = self.base_dir / "file.with.dots.txt"
        test_file.write_text("test")
        result = safe_unlink_under(str(test_file), self.base_dir)
        self.assertTrue(result)
        self.assertFalse(test_file.exists())


class ValidateProviderIdTest(TestCase):
    def test_allows_valid_id(self) -> None:
        """允许有效的 provider ID。"""
        self.assertEqual(validate_provider_id("my-provider"), "my-provider")
        self.assertEqual(validate_provider_id("provider123"), "provider123")
        self.assertEqual(validate_provider_id("a"), "a")

    def test_rejects_slash(self) -> None:
        """拒绝包含 / 的 ID。"""
        with self.assertRaises(HTTPException) as ctx:
            validate_provider_id("provider/../../etc/passwd")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_dotdot(self) -> None:
        """拒绝包含 .. 的 ID。"""
        with self.assertRaises(HTTPException) as ctx:
            validate_provider_id("provider..")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_space(self) -> None:
        """拒绝包含空格的 ID。"""
        with self.assertRaises(HTTPException) as ctx:
            validate_provider_id("provider with space")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_special_chars(self) -> None:
        """拒绝特殊字符。"""
        with self.assertRaises(HTTPException):
            validate_provider_id("provider@#$")
        with self.assertRaises(HTTPException):
            validate_provider_id("provider!()")
        with self.assertRaises(HTTPException):
            validate_provider_id("provider<>")

    def test_normalizes_uppercase(self) -> None:
        """大写会规范化为小写。"""
        result = validate_provider_id("MyProvider")
        self.assertEqual(result, "myprovider")

    def test_rejects_empty_string(self) -> None:
        """拒绝空字符串。"""
        with self.assertRaises(HTTPException):
            validate_provider_id("")

    def test_rejects_too_long(self) -> None:
        """拒绝超过 64 字符的 ID。"""
        long_id = "a" * 65
        with self.assertRaises(HTTPException):
            validate_provider_id(long_id)

    def test_allows_max_length(self) -> None:
        """允许正好 64 字符的 ID。"""
        max_id = "a" * 64
        self.assertEqual(validate_provider_id(max_id), max_id)

    def test_strips_whitespace(self) -> None:
        """去除前后空格后验证。"""
        result = validate_provider_id("  provider  ")
        self.assertEqual(result, "provider")

    def test_strips_newlines(self) -> None:
        """strip() 会去除首尾换行符，剩余部分合法则通过。"""
        self.assertEqual(validate_provider_id("provider\n"), "provider")
        self.assertEqual(validate_provider_id("provider\r\n"), "provider")
        self.assertEqual(validate_provider_id("\nprovider\n"), "provider")

