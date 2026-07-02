"""Downloader atomic write and remote media fetch tests."""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("AUTO_DOWNLOAD_GENERATED", "true")
os.environ.setdefault("SILICONFLOW_API_KEY", "sf-test-secret-value")
os.environ.setdefault("MEDIA_DOWNLOAD_MAX_BYTES", "10485760")

import angemedia_gateway.config as C
from angemedia_gateway.media import (
    cleanup_controlled_download_tmp_dir,
    download_remote_media,
    extension_from_response,
    stable_filename,
    try_download_remote_media,
    verify_download_tmp_os_replace_ready,
)
from angemedia_gateway.state import (
    generation_metadata_by_filename,
    known_generated_local_paths,
)


class DownloadTimeoutLimitsTest(unittest.TestCase):
    """验证 fetch_public_remote_media 传入正确的 httpx.Timeout 和 httpx.Limits。"""

    def _make_fake_client(self, captured: dict):
        """返回一个 fake httpx.AsyncClient 工厂，捕获 timeout/limits 并正常完成下载。"""

        async def _async_iter_chunks(chunks):
            for chunk in chunks:
                yield chunk

        def fake_client(**kwargs):
            captured["timeout"] = kwargs.get("timeout")
            captured["limits"] = kwargs.get("limits")

            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            fake_response = AsyncMock()
            fake_response.status_code = 200
            fake_response.headers = {"content-type": "image/png", "content-length": "4"}
            fake_response.raise_for_status = lambda: None
            fake_response.aiter_bytes = MagicMock(return_value=_async_iter_chunks([b"test"]))
            fake_response.aclose = AsyncMock()

            mock_client.build_request = MagicMock(return_value="fake-request")
            mock_client.send = AsyncMock(return_value=fake_response)
            return mock_client

        return fake_client

    def test_timeout_object_values(self):
        async def _run():
            import httpx
            captured = {}
            with patch("angemedia_gateway.media.httpx.AsyncClient", side_effect=self._make_fake_client(captured)):
                with patch("angemedia_gateway.media.validate_public_http_url", return_value="http://example.com/test.png"):
                    from angemedia_gateway.media import fetch_public_remote_media
                    result = await fetch_public_remote_media("http://example.com/test.png")
            self.assertIsInstance(result[0], bytes)
            self.assertIsInstance(captured["timeout"], httpx.Timeout)
            self.assertEqual(captured["timeout"].connect, C.MEDIA_DOWNLOAD_CONNECT_TIMEOUT)
            self.assertEqual(captured["timeout"].read, C.MEDIA_DOWNLOAD_READ_TIMEOUT)
            self.assertEqual(captured["timeout"].write, C.MEDIA_DOWNLOAD_WRITE_TIMEOUT)
            self.assertEqual(captured["timeout"].pool, C.MEDIA_DOWNLOAD_POOL_TIMEOUT)
        asyncio.run(_run())

    def test_limits_object_values(self):
        async def _run():
            import httpx
            captured = {}
            with patch("angemedia_gateway.media.httpx.AsyncClient", side_effect=self._make_fake_client(captured)):
                with patch("angemedia_gateway.media.validate_public_http_url", return_value="http://example.com/test.png"):
                    from angemedia_gateway.media import fetch_public_remote_media
                    await fetch_public_remote_media("http://example.com/test.png")
            self.assertIsInstance(captured["limits"], httpx.Limits)
            self.assertEqual(captured["limits"].max_connections, C.MEDIA_DOWNLOAD_CONCURRENCY)
            self.assertEqual(captured["limits"].max_keepalive_connections, C.MEDIA_DOWNLOAD_CONCURRENCY)
        asyncio.run(_run())

    def test_default_concurrency_is_one(self):
        self.assertEqual(C.MEDIA_DOWNLOAD_CONCURRENCY, 1)


class ExtensionFromResponseTest(unittest.TestCase):
    def test_png_content_type(self):
        ext = extension_from_response("http://example.com/file", "image/png", ".bin")
        self.assertEqual(ext, ".png")

    def test_jpeg_content_type(self):
        ext = extension_from_response("http://example.com/file", "image/jpeg", ".bin")
        self.assertEqual(ext, ".jpg")

    def test_mp4_content_type(self):
        ext = extension_from_response("http://example.com/file", "video/mp4", ".bin")
        self.assertEqual(ext, ".mp4")

    def test_octet_stream_falls_back_to_suffix(self):
        ext = extension_from_response("http://example.com/photo.png", "application/octet-stream", ".bin")
        self.assertEqual(ext, ".png")

    def test_octet_stream_no_suffix_uses_fallback(self):
        ext = extension_from_response("http://example.com/file", "application/octet-stream", ".bin")
        self.assertEqual(ext, ".bin")

    def test_content_type_with_charset(self):
        ext = extension_from_response("http://example.com/file", "image/webp; charset=utf-8", ".bin")
        self.assertEqual(ext, ".webp")

    def test_url_suffix_used_when_no_content_type_match(self):
        ext = extension_from_response("http://example.com/image.webp", "text/html", ".bin")
        self.assertEqual(ext, ".webp")


class StableFilenameTest(unittest.TestCase):
    def test_deterministic(self):
        a = stable_filename("image", "http://example.com/test.png", ".png")
        b = stable_filename("image", "http://example.com/test.png", ".png")
        self.assertEqual(a, b)

    def test_different_urls_produce_different_names(self):
        a = stable_filename("image", "http://example.com/a.png", ".png")
        b = stable_filename("image", "http://example.com/b.png", ".png")
        self.assertNotEqual(a, b)

    def test_prefix_sanitized(self):
        name = stable_filename("image/provider", "http://example.com/test.png", ".png")
        self.assertTrue(name.startswith("image-provider-"))

    def test_repeated_media_token_collapsed(self):
        image = stable_filename("image_agnes_image", "http://example.com/test.png", ".png")
        video = stable_filename("video_agnes_video", "http://example.com/test.mp4", ".mp4")
        self.assertTrue(image.startswith("image-agnes-"))
        self.assertTrue(video.startswith("video-agnes-"))
        self.assertNotIn("image-agnes-image", image)
        self.assertNotIn("video-agnes-video", video)

    def test_stable_id_overrides_url(self):
        a = stable_filename("image", "http://example.com/a.png", ".png", stable_id="fixed-id")
        b = stable_filename("image", "http://example.com/b.png", ".png", stable_id="fixed-id")
        self.assertEqual(a, b)


class ControlledTmpCleanupTest(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp(prefix="controlled-tmp-cleanup-test-")
        self._orig_output_dir = C.OUTPUT_DIR
        self._orig_upload_dir = C.UPLOAD_DIR
        C.OUTPUT_DIR = Path(self._tmp_dir) / "output"
        C.UPLOAD_DIR = Path(self._tmp_dir) / "uploads"
        C.OUTPUT_DIR.mkdir()
        C.UPLOAD_DIR.mkdir()

    def tearDown(self):
        C.OUTPUT_DIR = self._orig_output_dir
        C.UPLOAD_DIR = self._orig_upload_dir
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_removes_direct_part_and_copying_files(self):
        tmp_dir = C.OUTPUT_DIR / ".tmp"
        tmp_dir.mkdir()
        part = tmp_dir / "a.part"
        copying = tmp_dir / "a.copying"
        part.write_bytes(b"part")
        copying.write_bytes(b"copying")

        result = cleanup_controlled_download_tmp_dir()

        self.assertFalse(part.exists())
        self.assertFalse(copying.exists())
        self.assertEqual(result["part_removed"], 1)
        self.assertEqual(result["copying_removed"], 1)
        self.assertEqual(result["errors"], 0)

    def test_preserves_final_upload_nested_and_other_suffixes(self):
        tmp_dir = C.OUTPUT_DIR / ".tmp"
        nested = tmp_dir / "nested"
        nested.mkdir(parents=True)
        final_file = C.OUTPUT_DIR / "final.png"
        upload_file = C.UPLOAD_DIR / "upload.png"
        nested_part = nested / "a.part"
        keep_tmp = tmp_dir / "keep.tmp"
        readme = tmp_dir / "readme.txt"
        final_file.write_bytes(b"final")
        upload_file.write_bytes(b"upload")
        nested_part.write_bytes(b"nested")
        keep_tmp.write_bytes(b"tmp")
        readme.write_text("readme", encoding="utf-8")

        result = cleanup_controlled_download_tmp_dir()

        self.assertTrue(final_file.exists())
        self.assertTrue(upload_file.exists())
        self.assertTrue(nested_part.exists())
        self.assertTrue(keep_tmp.exists())
        self.assertTrue(readme.exists())
        self.assertEqual(result["part_removed"], 0)
        self.assertEqual(result["copying_removed"], 0)
        self.assertEqual(result["errors"], 0)

    def test_missing_tmp_dir_returns_zero_counts(self):
        result = cleanup_controlled_download_tmp_dir()

        self.assertEqual(result["part_removed"], 0)
        self.assertEqual(result["copying_removed"], 0)
        self.assertEqual(result["errors"], 0)

    def test_tmp_path_that_is_not_directory_is_not_deleted(self):
        tmp_path = C.OUTPUT_DIR / ".tmp"
        tmp_path.write_text("not a directory", encoding="utf-8")

        result = cleanup_controlled_download_tmp_dir()

        self.assertTrue(tmp_path.exists())
        self.assertEqual(result["part_removed"], 0)
        self.assertEqual(result["copying_removed"], 0)
        self.assertEqual(result["errors"], 1)

    def test_tmp_symlink_is_not_followed(self):
        target_dir = C.OUTPUT_DIR / "target-tmp"
        target_dir.mkdir()
        target_part = target_dir / "a.part"
        target_part.write_bytes(b"target")
        tmp_link = C.OUTPUT_DIR / ".tmp"
        try:
            tmp_link.symlink_to(target_dir, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        result = cleanup_controlled_download_tmp_dir()

        self.assertTrue(target_part.exists())
        self.assertEqual(result["part_removed"], 0)
        self.assertEqual(result["copying_removed"], 0)
        self.assertEqual(result["errors"], 1)

    def test_unlink_failure_reports_error_and_continues(self):
        tmp_dir = C.OUTPUT_DIR / ".tmp"
        tmp_dir.mkdir()
        locked = tmp_dir / "locked.part"
        removable = tmp_dir / "done.copying"
        locked.write_bytes(b"locked")
        removable.write_bytes(b"done")
        original_unlink = Path.unlink

        def maybe_fail(path_self, *args, **kwargs):
            if path_self.name == "locked.part":
                raise OSError("locked")
            return original_unlink(path_self, *args, **kwargs)

        with patch("angemedia_gateway.media.Path.unlink", autospec=True, side_effect=maybe_fail):
            result = cleanup_controlled_download_tmp_dir()

        self.assertTrue(locked.exists())
        self.assertFalse(removable.exists())
        self.assertEqual(result["part_removed"], 0)
        self.assertEqual(result["copying_removed"], 1)
        self.assertEqual(result["errors"], 1)


class RuntimeStartupCleanupWiringTest(unittest.TestCase):
    def test_runtime_invokes_controlled_download_tmp_cleanup(self):
        runtime_source = (ROOT / "scripts" / "angemedia_gateway" / "runtime.py").read_text(encoding="utf-8")

        self.assertIn("from .media import cleanup_controlled_download_tmp_dir", runtime_source)
        self.assertIn("cleanup_controlled_download_tmp_dir()", runtime_source)

    def test_runtime_invokes_same_filesystem_self_test(self):
        runtime_source = (ROOT / "scripts" / "angemedia_gateway" / "runtime.py").read_text(encoding="utf-8")

        self.assertIn("verify_download_tmp_os_replace_ready", runtime_source)
        self.assertIn("verify_download_tmp_os_replace_ready()", runtime_source)
        cleanup_pos = runtime_source.index("cleanup_controlled_download_tmp_dir()")
        selftest_pos = runtime_source.index("verify_download_tmp_os_replace_ready()")
        init_db_pos = runtime_source.index("init_db()")
        self.assertLess(cleanup_pos, selftest_pos)
        self.assertLess(selftest_pos, init_db_pos)


class SameFilesystemSelfTest(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp(prefix="samefs-test-")
        self._orig_output_dir = C.OUTPUT_DIR
        C.OUTPUT_DIR = Path(self._tmp_dir) / "output"
        C.OUTPUT_DIR.mkdir()

    def tearDown(self):
        C.OUTPUT_DIR = self._orig_output_dir
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_success_no_exception_no_residual(self):
        verify_download_tmp_os_replace_ready()
        tmp_dir = C.OUTPUT_DIR / ".tmp"
        self.assertTrue(tmp_dir.is_dir())
        self.assertEqual(len(list(tmp_dir.glob("samefs_*.tmp"))), 0)
        self.assertEqual(len(list(C.OUTPUT_DIR.glob("samefs_*.test"))), 0)

    def test_real_file_not_deleted(self):
        real_file = C.OUTPUT_DIR / "real_asset.png"
        real_file.write_bytes(b"real")
        verify_download_tmp_os_replace_ready()
        self.assertTrue(real_file.exists())
        self.assertEqual(real_file.read_bytes(), b"real")

    def test_exdev_raises_runtime_error(self):
        import errno as errno_mod

        def failing_replace(src, dst):
            raise OSError(errno_mod.EXDEV, "Invalid cross-device link")

        with patch("angemedia_gateway.media.os.replace", side_effect=failing_replace):
            with self.assertRaises(RuntimeError) as ctx:
                verify_download_tmp_os_replace_ready()
            msg = str(ctx.exception)
            self.assertIn("EXDEV", msg)
            self.assertIn("OUTPUT_DIR", msg)
            self.assertIn(".tmp", msg)
        tmp_dir = C.OUTPUT_DIR / ".tmp"
        self.assertEqual(len(list(tmp_dir.glob("samefs_*.tmp"))), 0)
        self.assertEqual(len(list(C.OUTPUT_DIR.glob("samefs_*.test"))), 0)

    def test_other_oserror_raises_runtime_error(self):
        import errno as errno_mod

        def failing_replace(src, dst):
            raise OSError(errno_mod.EIO, "I/O error")

        with patch("angemedia_gateway.media.os.replace", side_effect=failing_replace):
            with self.assertRaises(RuntimeError) as ctx:
                verify_download_tmp_os_replace_ready()
            msg = str(ctx.exception)
            self.assertIn("errno=", msg)
            self.assertIn("OUTPUT_DIR", msg)
            self.assertIn(".tmp", msg)
        tmp_dir = C.OUTPUT_DIR / ".tmp"
        self.assertEqual(len(list(tmp_dir.glob("samefs_*.tmp"))), 0)
        self.assertEqual(len(list(C.OUTPUT_DIR.glob("samefs_*.test"))), 0)

    def test_tmp_symlink_raises_runtime_error(self):
        target_dir = C.OUTPUT_DIR / "target"
        target_dir.mkdir()
        (target_dir / "keep.txt").write_bytes(b"keep")
        tmp_path = C.OUTPUT_DIR / ".tmp"
        try:
            tmp_path.symlink_to(target_dir, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        with self.assertRaises(RuntimeError) as ctx:
            verify_download_tmp_os_replace_ready()
        self.assertIn(".tmp", str(ctx.exception))
        self.assertTrue((target_dir / "keep.txt").exists())

    def test_tmp_is_regular_file_raises_runtime_error(self):
        tmp_path = C.OUTPUT_DIR / ".tmp"
        tmp_path.write_text("not a directory")

        with self.assertRaises(RuntimeError) as ctx:
            verify_download_tmp_os_replace_ready()
        self.assertIn(".tmp", str(ctx.exception))
        self.assertTrue(tmp_path.exists())
        self.assertTrue(tmp_path.is_file())


class _AtomicWriteTestBase(unittest.TestCase):
    """Shared setUp/tearDown for atomic write tests."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp(prefix="atomic-write-test-")
        self._orig_output_dir = C.OUTPUT_DIR
        self._orig_auto_download = C.AUTO_DOWNLOAD_GENERATED
        C.OUTPUT_DIR = Path(self._tmp_dir) / "output"
        C.OUTPUT_DIR.mkdir()
        C.AUTO_DOWNLOAD_GENERATED = True

    def tearDown(self):
        C.OUTPUT_DIR = self._orig_output_dir
        C.AUTO_DOWNLOAD_GENERATED = self._orig_auto_download
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _count_tmp_parts(self) -> int:
        tmp_dir = C.OUTPUT_DIR / ".tmp"
        if not tmp_dir.exists():
            return 0
        return len(list(tmp_dir.glob("*.part")))

    def _stable_filename(self, url: str, stable_id: str) -> str:
        return stable_filename("image_test", url, ".png", stable_id=stable_id)

    def _stream_response(self, content: bytes, content_type: str, final_url: str):
        async def _helper(url: str, tmp_path: Path):
            with tmp_path.open("wb") as fh:
                fh.write(content)
            return content_type, final_url

        return _helper

    def _streaming_client_patch(
        self,
        *,
        chunks: list[bytes],
        content_type: str = "image/png",
        content_length: int | None = None,
    ):
        async def _async_iter_chunks():
            for chunk in chunks:
                yield chunk

        def fake_client(**kwargs):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            fake_response = AsyncMock()
            fake_response.status_code = 200
            headers = {"content-type": content_type}
            if content_length is not None:
                headers["content-length"] = str(content_length)
            fake_response.headers = headers
            fake_response.raise_for_status = lambda: None
            fake_response.aiter_bytes = MagicMock(return_value=_async_iter_chunks())
            fake_response.aclose = AsyncMock()

            mock_client.build_request = MagicMock(return_value="fake-request")
            mock_client.send = AsyncMock(return_value=fake_response)
            return mock_client

        return patch("angemedia_gateway.media.httpx.AsyncClient", side_effect=fake_client)


class StreamingDownloadToPathTest(_AtomicWriteTestBase):
    def test_download_remote_media_does_not_call_fetch_public_remote_media(self):
        async def _run():
            stable_id = "stream-no-fetch"
            remote_url = "http://example.com/stream-no-fetch.png"
            expected = self._stable_filename(remote_url, stable_id)
            final_path = C.OUTPUT_DIR / expected

            with self._streaming_client_patch(chunks=[b"ok"]), \
                patch("angemedia_gateway.media.validate_public_http_url", return_value=remote_url), \
                patch(
                    "angemedia_gateway.media.fetch_public_remote_media",
                    new_callable=AsyncMock,
                    side_effect=AssertionError("download_remote_media must stream directly"),
                ):
                local_url, local_path = await download_remote_media(
                    remote_url,
                    prefix="image_test", fallback_ext=".png", stable_id=stable_id,
                )

            self.assertEqual(final_path.read_bytes(), b"ok")
            self.assertEqual(local_path, str(final_path))
            self.assertEqual(local_url, f"{C.PUBLIC_BASE_URL}/generated/{expected}")
        asyncio.run(_run())

    def test_download_remote_media_does_not_use_path_write_bytes(self):
        async def _run():
            stable_id = "stream-no-write-bytes"
            remote_url = "http://example.com/stream-no-write-bytes.png"
            expected = self._stable_filename(remote_url, stable_id)
            final_path = C.OUTPUT_DIR / expected

            with self._streaming_client_patch(chunks=[b"ok"]), \
                patch("angemedia_gateway.media.validate_public_http_url", return_value=remote_url), \
                patch("angemedia_gateway.media.Path.write_bytes", side_effect=AssertionError("write_bytes should not be used")):
                await download_remote_media(
                    remote_url,
                    prefix="image_test", fallback_ext=".png", stable_id=stable_id,
                )

            self.assertEqual(final_path.read_bytes(), b"ok")
        asyncio.run(_run())

    def test_multi_chunk_streaming_success(self):
        async def _run():
            stable_id = "stream-multi"
            remote_url = "http://example.com/stream-multi.png"
            expected = self._stable_filename(remote_url, stable_id)
            final_path = C.OUTPUT_DIR / expected

            with self._streaming_client_patch(chunks=[b"aa", b"bb", b"cc"]), \
                patch("angemedia_gateway.media.validate_public_http_url", return_value=remote_url):
                await download_remote_media(
                    remote_url,
                    prefix="image_test", fallback_ext=".png", stable_id=stable_id,
                )

            self.assertEqual(final_path.read_bytes(), b"aabbcc")
            self.assertEqual(self._count_tmp_parts(), 0)
        asyncio.run(_run())

    def test_streaming_over_limit_cleans_part(self):
        async def _run():
            stable_id = "stream-over-limit"
            remote_url = "http://example.com/stream-over-limit.png"
            expected = self._stable_filename(remote_url, stable_id)
            final_path = C.OUTPUT_DIR / expected

            with self._streaming_client_patch(chunks=[b"aaa", b"bbb"]), \
                patch("angemedia_gateway.media.validate_public_http_url", return_value=remote_url), \
                patch("angemedia_gateway.media.C.MEDIA_DOWNLOAD_MAX_BYTES", 4):
                with self.assertRaises(RuntimeError):
                    await download_remote_media(
                        remote_url,
                        prefix="image_test", fallback_ext=".png", stable_id=stable_id,
                    )

            self.assertFalse(final_path.exists())
            self.assertEqual(self._count_tmp_parts(), 0)
        asyncio.run(_run())


# ── 1. 正常下载 ──────────────────────────────────────────

class AtomicWriteSuccessTest(_AtomicWriteTestBase):
    def test_final_file_exists(self):
        async def _run():
            content = b"test image content"
            stable_id = "success-id-1"
            expected = self._stable_filename("http://example.com/ok.png", stable_id)
            final_path = C.OUTPUT_DIR / expected

            with patch(
                "angemedia_gateway.media._stream_public_remote_media_to_path",
                side_effect=self._stream_response(content, "image/png", "http://example.com/ok.png"),
            ):
                await download_remote_media(
                    "http://example.com/ok.png",
                    prefix="image_test", fallback_ext=".png", stable_id=stable_id,
                )
            self.assertTrue(final_path.exists())
        asyncio.run(_run())

    def test_content_complete(self):
        async def _run():
            content = b"x" * 2048
            stable_id = "success-id-2"
            expected = self._stable_filename("http://example.com/full.png", stable_id)
            final_path = C.OUTPUT_DIR / expected

            with patch(
                "angemedia_gateway.media._stream_public_remote_media_to_path",
                side_effect=self._stream_response(content, "image/png", "http://example.com/full.png"),
            ):
                await download_remote_media(
                    "http://example.com/full.png",
                    prefix="image_test", fallback_ext=".png", stable_id=stable_id,
                )
            self.assertEqual(final_path.read_bytes(), content)
            self.assertEqual(final_path.stat().st_size, len(content))
        asyncio.run(_run())

    def test_no_tmp_part_residual(self):
        async def _run():
            stable_id = "success-id-3"
            with patch(
                "angemedia_gateway.media._stream_public_remote_media_to_path",
                side_effect=self._stream_response(b"data", "image/png", "http://example.com/clean.png"),
            ):
                await download_remote_media(
                    "http://example.com/clean.png",
                    prefix="image_test", fallback_ext=".png", stable_id=stable_id,
                )
            self.assertEqual(self._count_tmp_parts(), 0)
        asyncio.run(_run())

    def test_local_url_and_path_no_tmp(self):
        async def _run():
            stable_id = "success-id-4"
            with patch(
                "angemedia_gateway.media._stream_public_remote_media_to_path",
                side_effect=self._stream_response(b"data", "image/png", "http://example.com/ret.png"),
            ):
                local_url, local_path = await download_remote_media(
                    "http://example.com/ret.png",
                    prefix="image_test", fallback_ext=".png", stable_id=stable_id,
                )
            self.assertNotIn(".tmp", local_url)
            self.assertNotIn(".tmp", local_path)
            self.assertNotIn(".part", local_url)
            self.assertNotIn(".part", local_path)
        asyncio.run(_run())


# ── 2. streaming helper 抛异常 ─────────────────

class AtomicWriteFetchFailureTest(_AtomicWriteTestBase):
    def test_final_file_not_created(self):
        async def _run():
            stable_id = "fetch-fail-1"
            with patch(
                "angemedia_gateway.media._stream_public_remote_media_to_path",
                side_effect=RuntimeError("network error"),
            ):
                with self.assertRaises(RuntimeError):
                    await download_remote_media(
                        "http://example.com/fail1.png",
                        prefix="image_test", fallback_ext=".png", stable_id=stable_id,
                    )
            expected = self._stable_filename("http://example.com/fail1.png", stable_id)
            self.assertFalse((C.OUTPUT_DIR / expected).exists())
        asyncio.run(_run())

    def test_part_cleaned_up(self):
        async def _run():
            stable_id = "fetch-fail-2"
            with patch(
                "angemedia_gateway.media._stream_public_remote_media_to_path",
                side_effect=RuntimeError("timeout"),
            ):
                with self.assertRaises(RuntimeError):
                    await download_remote_media(
                        "http://example.com/fail2.png",
                        prefix="image_test", fallback_ext=".png", stable_id=stable_id,
                    )
            self.assertEqual(self._count_tmp_parts(), 0)
        asyncio.run(_run())

    def test_try_download_returns_original_url(self):
        async def _run():
            remote_url = "http://example.com/fallback.png"
            with patch(
                "angemedia_gateway.media._stream_public_remote_media_to_path",
                side_effect=RuntimeError("download failed"),
            ):
                local_url, local_path, error = await try_download_remote_media(
                    remote_url, prefix="image_test", fallback_ext=".png", stable_id="fb-1",
                )
            self.assertEqual(local_url, remote_url)
            self.assertEqual(local_path, "")
            self.assertIsNotNone(error)
            self.assertIn("download failed", error)
        asyncio.run(_run())


# ── 3. os.replace 抛 OSError ──────────────────────────────

class AtomicWriteReplaceFailureTest(_AtomicWriteTestBase):
    def test_final_file_not_created(self):
        async def _run():
            stable_id = "replace-fail-1"

            def failing_replace(src, dst):
                raise OSError("permission denied")

            with patch(
                "angemedia_gateway.media._stream_public_remote_media_to_path",
                side_effect=self._stream_response(b"data", "image/png", "http://example.com/rf1.png"),
            ), patch("angemedia_gateway.media.os.replace", side_effect=failing_replace):
                with self.assertRaises(OSError):
                    await download_remote_media(
                        "http://example.com/rf1.png",
                        prefix="image_test", fallback_ext=".png", stable_id=stable_id,
                    )
            expected = self._stable_filename("http://example.com/rf1.png", stable_id)
            self.assertFalse((C.OUTPUT_DIR / expected).exists())
        asyncio.run(_run())

    def test_part_cleaned_up(self):
        async def _run():
            stable_id = "replace-fail-2"

            def failing_replace(src, dst):
                raise OSError("disk full")

            with patch(
                "angemedia_gateway.media._stream_public_remote_media_to_path",
                side_effect=self._stream_response(b"data", "image/png", "http://example.com/rf2.png"),
            ), patch("angemedia_gateway.media.os.replace", side_effect=failing_replace):
                with self.assertRaises(OSError):
                    await download_remote_media(
                        "http://example.com/rf2.png",
                        prefix="image_test", fallback_ext=".png", stable_id=stable_id,
                    )
            self.assertEqual(self._count_tmp_parts(), 0)
        asyncio.run(_run())

    def test_try_download_returns_original_url(self):
        async def _run():
            remote_url = "http://example.com/replace-fb.png"

            def failing_replace(src, dst):
                raise OSError("I/O error")

            with patch(
                "angemedia_gateway.media._stream_public_remote_media_to_path",
                side_effect=self._stream_response(b"data", "image/png", remote_url),
            ), patch("angemedia_gateway.media.os.replace", side_effect=failing_replace):
                local_url, local_path, error = await try_download_remote_media(
                    remote_url, prefix="image_test", fallback_ext=".png", stable_id="rfb-1",
                )
            self.assertEqual(local_url, remote_url)
            self.assertEqual(local_path, "")
            self.assertIsNotNone(error)
        asyncio.run(_run())


# ── 4. final 文件已存在 ───────────────────────────────────

class AtomicWriteExistingFileTest(_AtomicWriteTestBase):
    def test_no_overwrite(self):
        async def _run():
            stable_id = "existing-1"
            expected = self._stable_filename("http://example.com/exist.png", stable_id)
            final_path = C.OUTPUT_DIR / expected
            original = b"original content"
            final_path.write_bytes(original)

            with patch(
                "angemedia_gateway.media._stream_public_remote_media_to_path",
                side_effect=self._stream_response(b"new content", "image/png", "http://example.com/exist.png"),
            ):
                await download_remote_media(
                    "http://example.com/exist.png",
                    prefix="image_test", fallback_ext=".png", stable_id=stable_id,
                )
            self.assertEqual(final_path.read_bytes(), original)
        asyncio.run(_run())

    def test_no_part_residual(self):
        async def _run():
            stable_id = "existing-2"
            expected = self._stable_filename("http://example.com/exist2.png", stable_id)
            final_path = C.OUTPUT_DIR / expected
            final_path.write_bytes(b"keep")

            with patch(
                "angemedia_gateway.media._stream_public_remote_media_to_path",
                side_effect=self._stream_response(b"new", "image/png", "http://example.com/exist2.png"),
            ):
                await download_remote_media(
                    "http://example.com/exist2.png",
                    prefix="image_test", fallback_ext=".png", stable_id=stable_id,
                )
            self.assertEqual(self._count_tmp_parts(), 0)
        asyncio.run(_run())


# ── 5. .tmp 目录不进入 listing ────────────────────────────

class TmpNotInListingTest(unittest.TestCase):
    def test_known_generated_local_paths_excludes_tmp(self):
        paths = known_generated_local_paths()
        for p in paths:
            if p:
                self.assertNotIn("/.tmp/", p.replace("\\", "/"))

    def test_generation_metadata_by_filename_excludes_tmp(self):
        metadata = generation_metadata_by_filename()
        for key in metadata:
            self.assertNotIn(".tmp", key)
            self.assertNotIn(".part", key)


if __name__ == "__main__":
    unittest.main()


# ── redirect 链路 SSRF 测试 ──────────────────────────

from angemedia_gateway.media import _send_public_get


class SendPublicGetRedirectTest(unittest.TestCase):
    """_send_public_get redirect 链路安全测试。"""

    def setUp(self):
        self._real_getaddrinfo = socket.getaddrinfo

        def _host_sensitive_getaddrinfo(host, port, *args, **kwargs):
            if host == "example.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port))]
            return self._real_getaddrinfo(host, port, *args, **kwargs)

        self._patcher = patch("socket.getaddrinfo", side_effect=_host_sensitive_getaddrinfo)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _make_fake_client(self, redirects):
        """创建 fake AsyncClient，按 redirects 列表返回 302，最后一个返回 200。"""

        call_count = 0

        async def _send(request, stream=True, follow_redirects=False):
            nonlocal call_count
            if call_count < len(redirects):
                loc = redirects[call_count]
                call_count += 1
                resp = AsyncMock()
                resp.status_code = 302
                resp.headers = {"location": loc} if loc else {}
                resp.aclose = AsyncMock()
                return resp
            resp = AsyncMock()
            resp.status_code = 200
            resp.headers = {"content-type": "image/png"}
            resp.aclose = AsyncMock()
            return resp

        mock = AsyncMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)
        mock.build_request = MagicMock(return_value="req")
        mock.send = _send
        return mock

    def test_redirect_to_private_ip_rejected(self):
        """redirect 到 127.0.0.1 应拒绝。"""
        async def _run():
            client = self._make_fake_client(["http://127.0.0.1/steal.png"])
            with self.assertRaises(ValueError) as ctx:
                await _send_public_get(client, "https://example.com/ok.png")
            self.assertIn("内网或保留地址", str(ctx.exception))
        asyncio.run(_run())

    def test_redirect_count_limit(self):
        """连续 redirect 超过 REMOTE_MEDIA_MAX_REDIRECTS 应失败。"""
        async def _run():
            from angemedia_gateway.media import REMOTE_MEDIA_MAX_REDIRECTS
            many_redirects = ["https://example.com/redir.png"] * (REMOTE_MEDIA_MAX_REDIRECTS + 2)
            client = self._make_fake_client(many_redirects)
            with self.assertRaises(RuntimeError) as ctx:
                await _send_public_get(client, "https://example.com/start.png")
            self.assertIn("重定向超过", str(ctx.exception))
        asyncio.run(_run())

    def test_redirect_missing_location(self):
        """302 无 Location header 应失败。"""
        async def _run():
            client = self._make_fake_client([None])
            with self.assertRaises(RuntimeError) as ctx:
                await _send_public_get(client, "https://example.com/start.png")
            self.assertIn("缺少 Location", str(ctx.exception))
        asyncio.run(_run())


class RemoteMediaClientProxyIsolationTest(unittest.TestCase):
    """确认远程媒体下载 client 不读取代理环境变量。"""

    def test_remote_media_http_client_trust_env_false(self):
        import httpx as httpx_mod
        from angemedia_gateway.media import _remote_media_http_client
        captured = {}

        original_init = httpx_mod.AsyncClient.__init__

        def capturing_init(self_client, **kwargs):
            captured.update(kwargs)
            original_init(self_client, **kwargs)

        with patch.object(httpx_mod.AsyncClient, "__init__", capturing_init):
            _remote_media_http_client()

        self.assertIn("trust_env", captured)
        self.assertFalse(captured["trust_env"])
