"""下载 / 上传大小限制纯函数测试。"""
from __future__ import annotations

import tempfile
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from fastapi import HTTPException
from starlette.datastructures import UploadFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.media import REMOTE_MEDIA_B64_MAX_BYTES, fetch_public_remote_media, maybe_to_b64
from angemedia_gateway.runtime import write_upload_file_limited


class FakeAsyncIterator:
    """模拟 httpx Response.aiter_bytes() 的异步迭代器。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


def _make_mock_response(
    *,
    chunks: list[bytes],
    content_length: int | None = None,
    status_code: int = 200,
) -> MagicMock:
    """构造假的 httpx.Response，支持 aiter_bytes 异步迭代。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {}
    if content_length is not None:
        resp.headers["content-length"] = str(content_length)
    resp.headers.setdefault("content-type", "image/png")
    resp.aiter_bytes = lambda chunk_size: FakeAsyncIterator(chunks)
    resp.aclose = AsyncMock()
    resp.raise_for_status = MagicMock()
    return resp


class FetchPublicRemoteMediaTest(IsolatedAsyncioTestCase):
    """测试 fetch_public_remote_media 的大小限制。"""

    async def test_rejects_content_length_over_limit(self) -> None:
        """Content-Length 超过限制时拒绝。"""
        over_limit = 200
        mock_resp = _make_mock_response(chunks=[b"x" * 50], content_length=over_limit)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.send = AsyncMock(return_value=mock_resp)

        with (
            patch("angemedia_gateway.media.C.MEDIA_DOWNLOAD_MAX_BYTES", 100),
            patch("angemedia_gateway.media.httpx.AsyncClient", return_value=mock_client),
            patch("angemedia_gateway.media._send_public_get", new_callable=AsyncMock) as mock_send,
        ):
            mock_send.return_value = (mock_resp, "http://example.com/test.png")
            with self.assertRaises(RuntimeError) as ctx:
                await fetch_public_remote_media("http://example.com/test.png")
            self.assertIn("远端媒体过大", str(ctx.exception))

    async def test_rejects_cumulative_read_over_limit(self) -> None:
        """累计读取超过限制时中断。"""
        chunk1 = b"a" * 60
        chunk2 = b"b" * 60
        mock_resp = _make_mock_response(chunks=[chunk1, chunk2])
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.send = AsyncMock(return_value=mock_resp)

        with (
            patch("angemedia_gateway.media.C.MEDIA_DOWNLOAD_MAX_BYTES", 100),
            patch("angemedia_gateway.media.httpx.AsyncClient", return_value=mock_client),
            patch("angemedia_gateway.media._send_public_get", new_callable=AsyncMock) as mock_send,
        ):
            mock_send.return_value = (mock_resp, "http://example.com/test.png")
            with self.assertRaises(RuntimeError) as ctx:
                await fetch_public_remote_media("http://example.com/test.png")
            self.assertIn("远端媒体过大", str(ctx.exception))

    async def test_within_limit_returns_data(self) -> None:
        """正常大小返回数据。"""
        data = b"hello"
        mock_resp = _make_mock_response(chunks=[data], content_length=5)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.send = AsyncMock(return_value=mock_resp)

        with (
            patch("angemedia_gateway.media.C.MEDIA_DOWNLOAD_MAX_BYTES", 1000),
            patch("angemedia_gateway.media.httpx.AsyncClient", return_value=mock_client),
            patch("angemedia_gateway.media._send_public_get", new_callable=AsyncMock) as mock_send,
        ):
            mock_send.return_value = (mock_resp, "http://example.com/test.png")
            content, content_type, final_url = await fetch_public_remote_media(
                "http://example.com/test.png"
            )
        self.assertEqual(content, data)
        self.assertIn("image", content_type)

    async def test_no_content_length_uses_cumulative(self) -> None:
        """无 Content-Length 时用累计读取判断。"""
        chunk1 = b"a" * 50
        chunk2 = b"b" * 50
        chunk3 = b"c" * 10
        mock_resp = _make_mock_response(chunks=[chunk1, chunk2, chunk3])
        # 不设 content_length
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.send = AsyncMock(return_value=mock_resp)

        with (
            patch("angemedia_gateway.media.C.MEDIA_DOWNLOAD_MAX_BYTES", 100),
            patch("angemedia_gateway.media.httpx.AsyncClient", return_value=mock_client),
            patch("angemedia_gateway.media._send_public_get", new_callable=AsyncMock) as mock_send,
        ):
            mock_send.return_value = (mock_resp, "http://example.com/test.png")
            with self.assertRaises(RuntimeError) as ctx:
                await fetch_public_remote_media("http://example.com/test.png")
            self.assertIn("远端媒体过大", str(ctx.exception))

    async def test_maybe_to_b64_uses_conservative_download_limit(self) -> None:
        result = {"data": [{"url": "https://cdn.example.com/image.png"}]}
        with patch("angemedia_gateway.media.fetch_public_remote_media", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = (b"image-bytes", "image/png", "https://cdn.example.com/image.png")
            converted = await maybe_to_b64(result, "b64_json")

        self.assertIn("b64_json", converted["data"][0])
        mock_fetch.assert_awaited_once_with(ANY, max_bytes=REMOTE_MEDIA_B64_MAX_BYTES)


def _fake_upload_file(data: bytes, filename: str = "test.png") -> UploadFile:
    """构造 starlette UploadFile，从内存字节读取。"""
    import io
    file_obj = io.BytesIO(data)
    upload = UploadFile(filename=filename, file=file_obj)
    return upload


class WriteUploadFileLimitedTest(IsolatedAsyncioTestCase):
    """测试 write_upload_file_limited 的大小限制与清理。"""

    async def test_normal_small_file_writes(self) -> None:
        """正常小文件写入成功。"""
        data = b"hello world"
        upload = _fake_upload_file(data)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.bin"
            total = await write_upload_file_limited(upload, path, max_bytes=1024)
            self.assertEqual(total, len(data))
            self.assertEqual(path.read_bytes(), data)

    async def test_over_limit_raises_413(self) -> None:
        """超过限制时抛出 HTTPException 413。"""
        data = b"x" * 200
        upload = _fake_upload_file(data)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.bin"
            with self.assertRaises(HTTPException) as ctx:
                await write_upload_file_limited(upload, path, max_bytes=100)
            self.assertEqual(ctx.exception.status_code, 413)
            self.assertIn("超过", ctx.exception.detail)

    async def test_over_limit_cleans_up(self) -> None:
        """超过限制后半成品文件被清理。"""
        data = b"x" * 200
        upload = _fake_upload_file(data)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.bin"
            with self.assertRaises(HTTPException):
                await write_upload_file_limited(upload, path, max_bytes=100)
            self.assertFalse(path.exists(), "超限后半成品文件应被删除")

    async def test_empty_file_writes_zero_bytes(self) -> None:
        """空文件写入返回 0。"""
        upload = _fake_upload_file(b"")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.bin"
            total = await write_upload_file_limited(upload, path, max_bytes=1024)
            self.assertEqual(total, 0)
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), b"")

    async def test_exact_limit_writes(self) -> None:
        """正好等于限制时写入成功。"""
        data = b"x" * 100
        upload = _fake_upload_file(data)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.bin"
            total = await write_upload_file_limited(upload, path, max_bytes=100)
            self.assertEqual(total, 100)
            self.assertEqual(path.read_bytes(), data)
