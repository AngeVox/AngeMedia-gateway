"""本地资产转 data URL、adapter 参考图转发、hash 隐私测试。"""
from __future__ import annotations

import base64
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.reference_images import (  # noqa: E402
    REFERENCE_IMAGE_MAX_BYTES,
    is_safe_image_data_url,
    local_asset_to_data_url,
    materialize_image_reference,
)
from angemedia_gateway.request_hash_builders import _reference_identity  # noqa: E402

REAL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
    b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)
REAL_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20 + b"\xff\xd9"
REAL_GIF = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"


class LocalAssetToDataUrlTest(unittest.TestCase):
    """local_asset_to_data_url 安全与功能测试。"""

    def setUp(self) -> None:
        self._tmp = Path(os.environ.get("TMP", os.environ.get("TEMP", "/tmp"))) / "angemedia_test_ref"
        self._generated = self._tmp / "generated"
        self._uploads = self._tmp / "uploads"
        self._generated.mkdir(parents=True, exist_ok=True)
        self._uploads.mkdir(parents=True, exist_ok=True)
        self._patchers = [
            patch("angemedia_gateway.reference_images.C.OUTPUT_DIR", self._generated),
            patch("angemedia_gateway.reference_images.C.UPLOAD_DIR", self._uploads),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self) -> None:
        for p in self._patchers:
            p.stop()
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_image(self, base: Path, name: str, content: bytes | None = None) -> Path:
        if content is None:
            content = REAL_PNG
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_generated_png_to_data_url(self) -> None:
        self._write_image(self._generated, "abc.png")
        result = local_asset_to_data_url("/generated/abc.png")
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("data:image/png;base64,"))

    def test_uploads_jpg_to_data_url(self) -> None:
        self._write_image(self._uploads, "photo.jpg", content=REAL_JPEG)
        result = local_asset_to_data_url("/uploads/photo.jpg")
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("data:image/jpeg;base64,"))

    def test_webp_mime(self) -> None:
        real_webp = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 20
        self._write_image(self._generated, "img.webp", content=real_webp)
        result = local_asset_to_data_url("/generated/img.webp")
        self.assertIsNotNone(result)
        self.assertIn("data:image/webp;", result)

    def test_nonexistent_file_returns_none(self) -> None:
        result = local_asset_to_data_url("/generated/missing.png")
        self.assertIsNone(result)

    def test_non_image_extension_returns_none(self) -> None:
        path = self._generated / "notes.txt"
        path.write_text("hello")
        result = local_asset_to_data_url("/generated/notes.txt")
        self.assertIsNone(result)

    def test_path_traversal_blocked(self) -> None:
        result = local_asset_to_data_url("/generated/../../etc/passwd")
        self.assertIsNone(result)

    def test_path_traversal_encoded_blocked(self) -> None:
        result = local_asset_to_data_url("/generated/../uploads/secret.png")
        self.assertIsNone(result)

    def test_empty_relative_path_returns_none(self) -> None:
        result = local_asset_to_data_url("/generated/")
        self.assertIsNone(result)

    def test_non_gateway_path_returns_none(self) -> None:
        result = local_asset_to_data_url("/etc/passwd")
        self.assertIsNone(result)

    def test_absolute_url_returns_none(self) -> None:
        result = local_asset_to_data_url("https://example.com/img.png")
        self.assertIsNone(result)

    def test_none_input_returns_none(self) -> None:
        result = local_asset_to_data_url(None)
        self.assertIsNone(result)

    def test_empty_string_returns_none(self) -> None:
        result = local_asset_to_data_url("")
        self.assertIsNone(result)

    def test_oversized_file_returns_none(self) -> None:
        big = b"\x00" * (REFERENCE_IMAGE_MAX_BYTES + 1)
        self._write_image(self._generated, "big.png", content=big)
        result = local_asset_to_data_url("/generated/big.png")
        self.assertIsNone(result)

    def test_zero_byte_file_returns_none(self) -> None:
        self._write_image(self._generated, "empty.png", content=b"")
        result = local_asset_to_data_url("/generated/empty.png")
        self.assertIsNone(result)

    def test_data_url_content_is_valid_base64(self) -> None:
        self._write_image(self._generated, "test.png", content=REAL_PNG)
        result = local_asset_to_data_url("/generated/test.png")
        self.assertIsNotNone(result)
        header, b64_part = result.split(",", 1)
        decoded = base64.b64decode(b64_part)
        self.assertEqual(decoded, REAL_PNG)

    def test_upload_dir_path_traversal_blocked(self) -> None:
        result = local_asset_to_data_url("/uploads/../../generated/abc.png")
        self.assertIsNone(result)

    def test_png_extension_but_fake_content_rejected(self) -> None:
        """MIME sniff 必须检查 magic bytes，不只是扩展名。"""
        self._write_image(self._generated, "fake.png", content=b"not-a-real-png-file-at-all")
        result = local_asset_to_data_url("/generated/fake.png")
        self.assertIsNone(result)

    def test_jpeg_extension_but_fake_content_rejected(self) -> None:
        self._write_image(self._generated, "fake.jpg", content=b"this is not jpeg data")
        result = local_asset_to_data_url("/generated/fake.jpg")
        self.assertIsNone(result)

    def test_real_jpeg_content_accepted(self) -> None:
        self._write_image(self._generated, "real.jpg", content=REAL_JPEG)
        result = local_asset_to_data_url("/generated/real.jpg")
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("data:image/jpeg;"))

    def test_real_gif_content_accepted(self) -> None:
        self._write_image(self._generated, "anim.gif", content=REAL_GIF)
        result = local_asset_to_data_url("/generated/anim.gif")
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("data:image/gif;"))

    def test_webp_magic_requires_riff_webp(self) -> None:
        """WebP 必须同时有 RIFF 和 WEBP 标记。"""
        fake_riff = b"RIFF\x00\x00\x00\x00XXXX"
        self._write_image(self._generated, "fake.webp", content=fake_riff)
        result = local_asset_to_data_url("/generated/fake.webp")
        self.assertIsNone(result)

    def test_mime_from_content_not_extension_mismatch(self) -> None:
        """MIME 来自内容，不是扩展名。把 PNG 内容命名为 .jpg 仍返回 image/png。"""
        self._write_image(self._generated, "mismatch.jpg", content=REAL_PNG)
        result = local_asset_to_data_url("/generated/mismatch.jpg")
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("data:image/png;"))

    def test_safe_image_data_url_accepts_sniffed_image_content(self) -> None:
        data_url = "data:image/png;base64," + base64.b64encode(REAL_PNG).decode("ascii")
        self.assertTrue(is_safe_image_data_url(data_url))

    def test_safe_image_data_url_rejects_invalid_base64_or_mime_mismatch(self) -> None:
        mismatched = "data:image/jpeg;base64," + base64.b64encode(REAL_PNG).decode("ascii")
        self.assertFalse(is_safe_image_data_url("data:image/png;base64,not-base64!"))
        self.assertFalse(is_safe_image_data_url(mismatched))

    def test_materialize_image_reference_preserves_remote_and_data_urls(self) -> None:
        data_url = "data:image/png;base64," + base64.b64encode(REAL_PNG).decode("ascii")
        self.assertEqual(materialize_image_reference("https://example.com/ref.png"), "https://example.com/ref.png")
        self.assertEqual(materialize_image_reference(data_url), data_url)


class SiliconFlowDataUrlReferenceTest(unittest.TestCase):
    """SiliconFlow adapter 把本地路径转为 data URL 而非 PUBLIC_BASE_URL。"""

    def test_local_path_with_existing_file_uses_data_url(self) -> None:
        import tempfile
        from angemedia_gateway.providers.image.siliconflow import _provider_image_reference

        with tempfile.TemporaryDirectory() as tmp:
            gen_dir = Path(tmp) / "generated"
            gen_dir.mkdir()
            png_content = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
                b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
                b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            (gen_dir / "test.png").write_bytes(png_content)
            with patch("angemedia_gateway.providers.image.siliconflow.materialize_image_reference") as mock_convert:
                mock_convert.return_value = "data:image/png;base64,AAAA"
                result = _provider_image_reference("/generated/test.png")
                self.assertEqual(result, "data:image/png;base64,AAAA")
                mock_convert.assert_called_once_with("/generated/test.png")

    def test_local_path_without_file_fails_instead_of_using_public_url(self) -> None:
        from angemedia_gateway.providers.errors import BackendUnavailable
        from angemedia_gateway.providers.image.siliconflow import _provider_image_reference

        with patch(
            "angemedia_gateway.providers.image.siliconflow.materialize_image_reference",
            side_effect=ValueError("invalid local reference"),
        ):
            with self.assertRaises(BackendUnavailable):
                _provider_image_reference("/generated/missing.png")

    def test_remote_url_passes_through(self) -> None:
        from angemedia_gateway.providers.image.siliconflow import _provider_image_reference

        result = _provider_image_reference("https://example.com/img.png")
        self.assertEqual(result, "https://example.com/img.png")

    def test_none_returns_none(self) -> None:
        from angemedia_gateway.providers.image.siliconflow import _provider_image_reference

        result = _provider_image_reference(None)
        self.assertIsNone(result)


class ReferenceHashPrivacyTest(unittest.TestCase):
    """hash builder 不泄露 base64 内容或本地路径。"""

    def test_gateway_path_hashes_as_path_type(self) -> None:
        result = _reference_identity("/generated/abc123.png")
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "path")
        self.assertIn("/generated/abc123.png", result["path"])
        # 不应包含 base64 特征
        self.assertNotIn("base64", str(result))

    def test_data_url_hashes_as_sha256(self) -> None:
        data_url = "data:image/png;base64," + base64.b64encode(b"fake-image-data").decode()
        result = _reference_identity(data_url)
        self.assertIsNotNone(result)
        self.assertIn("sha256", result.get("type", ""))
        self.assertTrue(result["digest"].startswith("sha256:"))
        # 不应包含原始 base64 数据
        raw_b64 = base64.b64encode(b"fake-image-data").decode()
        self.assertNotIn(raw_b64, str(result))

    def test_remote_url_hashes_as_url_sha256(self) -> None:
        result = _reference_identity("https://example.com/image.png")
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "url_sha256")
        self.assertTrue(result["digest"].startswith("sha256:"))
        # 原始 URL 不应出现在结果中
        self.assertNotIn("example.com", str(result))

    def test_no_raw_base64_in_any_reference_output(self) -> None:
        large_content = b"x" * 10000
        data_url = "data:image/png;base64," + base64.b64encode(large_content).decode()
        result = _reference_identity(data_url)
        result_str = str(result)
        # 检查 base64 内容不在结果中
        self.assertLess(len(result_str), 200)


if __name__ == "__main__":
    unittest.main()
