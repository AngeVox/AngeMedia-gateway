"""上传文件时写入 assets 表的测试。"""
from __future__ import annotations

import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

from studio_test_client import TestClient  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
import angemedia_gateway.config as _C  # noqa: E402


class UploadAssetWriteTest(unittest.TestCase):
    """测试上传文件后自动写入 assets 表。"""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="upload-asset-test-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._output_dir = Path(self._tmp_dir) / "output"
        self._upload_dir = Path(self._tmp_dir) / "upload"
        self._output_dir.mkdir()
        self._upload_dir.mkdir()
        # 保存原始配置
        self._orig_db = _C.DB_FILE
        self._orig_output = _C.OUTPUT_DIR
        self._orig_upload = _C.UPLOAD_DIR
        self._orig_base_url = _C.PUBLIC_BASE_URL
        self._orig_admin_user = os.environ.get("ADMIN_USERNAME")
        self._orig_admin_pass = os.environ.get("ADMIN_DEFAULT_PASSWORD")
        # 覆盖配置
        _C.DB_FILE = self._db_path
        _C.OUTPUT_DIR = self._output_dir
        _C.UPLOAD_DIR = self._upload_dir
        _C.PUBLIC_BASE_URL = "http://testserver"
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_DEFAULT_PASSWORD"] = "admin123456"
        from angemedia_gateway.state import init_db, ensure_default_admin_user  # noqa: E402
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        _C.DB_FILE = self._orig_db
        _C.OUTPUT_DIR = self._orig_output
        _C.UPLOAD_DIR = self._orig_upload
        _C.PUBLIC_BASE_URL = self._orig_base_url
        if self._orig_admin_user is None:
            os.environ.pop("ADMIN_USERNAME", None)
        else:
            os.environ["ADMIN_USERNAME"] = self._orig_admin_user
        if self._orig_admin_pass is None:
            os.environ.pop("ADMIN_DEFAULT_PASSWORD", None)
        else:
            os.environ["ADMIN_DEFAULT_PASSWORD"] = self._orig_admin_pass
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def login_admin(self) -> None:
        resp = self.client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def _count_assets(self) -> int:
        conn = sqlite3.connect(str(self._db_path))
        try:
            return conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        finally:
            conn.close()

    def _read_asset_by_relative_path(self, relative_path: str) -> dict | None:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM assets WHERE storage_area = 'upload' AND relative_path = ?",
                (relative_path,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _make_upload(self, filename: str, content: bytes = b"fake data") -> dict:
        """上传单个文件并返回响应 JSON。"""
        resp = self.client.post(
            "/v1/uploads",
            files={"files": (filename, io.BytesIO(content), "application/octet-stream")},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_upload_metadata_reports_current_limit_without_weakening_post_boundary(self) -> None:
        self.login_admin()
        with patch.object(_C,'UPLOAD_MAX_FILES',2):
            response=self.client.get('/v1/uploads?limit=1')
            self.assertEqual(response.status_code,200);self.assertEqual(response.json()['limits'],{'max_files':2})
            files=[('files',(f'{i}.png',io.BytesIO(b'fixture'),'image/png')) for i in range(3)]
            rejected=self.client.post('/v1/uploads',files=files)
            self.assertEqual(rejected.status_code,413);self.assertEqual(self._count_assets(),0)

    def test_sixteen_images_can_be_saved_in_batches_under_existing_upload_limit(self) -> None:
        self.login_admin();paths=[]
        with patch.object(_C,'UPLOAD_MAX_FILES',10):
            limit=self.client.get('/v1/uploads?limit=1').json()['limits']['max_files']
            for start in range(0,16,limit):
                count=min(limit,16-start)
                files=[('files',(f'{i}.png',io.BytesIO(b'fixture'),'image/png')) for i in range(start,start+count)]
                response=self.client.post('/v1/uploads',files=files,data={'roles':','.join(['reference']*count)})
                self.assertEqual(response.status_code,200,response.text)
                paths.extend(row['url'] for row in response.json()['data'])
        self.assertEqual(len(paths),16);self.assertEqual(len(set(paths)),16);self.assertEqual(self._count_assets(),16)

    # ── 1. Image upload creates asset record ──────────

    def test_png_upload_creates_asset(self) -> None:
        """上传 .png 文件后产生 asset 记录。"""
        self.login_admin()
        self.assertEqual(self._count_assets(), 0)
        resp_json = self._make_upload("photo.png")
        self.assertEqual(self._count_assets(), 1)
        upload_filename = resp_json["data"][0]["filename"]
        asset = self._read_asset_by_relative_path(upload_filename)
        self.assertIsNotNone(asset)

    def test_jpg_upload_creates_asset(self) -> None:
        """上传 .jpg 文件后产生 asset 记录。"""
        self.login_admin()
        self._make_upload("image.jpg")
        self.assertEqual(self._count_assets(), 1)

    def test_jpeg_upload_creates_asset(self) -> None:
        """上传 .jpeg 文件后产生 asset 记录。"""
        self.login_admin()
        self._make_upload("photo.jpeg")
        self.assertEqual(self._count_assets(), 1)

    def test_webp_upload_creates_asset(self) -> None:
        """上传 .webp 文件后产生 asset 记录。"""
        self.login_admin()
        self._make_upload("art.webp")
        self.assertEqual(self._count_assets(), 1)

    def test_gif_upload_creates_asset(self) -> None:
        """上传 .gif 文件后产生 asset 记录。"""
        self.login_admin()
        self._make_upload("anim.gif")
        self.assertEqual(self._count_assets(), 1)

    # ── 2. Video upload creates asset record ──────────

    def test_mp4_upload_creates_asset(self) -> None:
        """上传 .mp4 文件后产生 asset 记录。"""
        self.login_admin()
        self._make_upload("clip.mp4")
        self.assertEqual(self._count_assets(), 1)

    def test_webm_upload_creates_asset(self) -> None:
        """上传 .webm 文件后产生 asset 记录。"""
        self.login_admin()
        self._make_upload("video.webm")
        self.assertEqual(self._count_assets(), 1)

    def test_mov_upload_creates_asset(self) -> None:
        """上传 .mov 文件后产生 asset 记录。"""
        self.login_admin()
        self._make_upload("footage.mov")
        self.assertEqual(self._count_assets(), 1)

    # ── 3. .bin upload does NOT create asset record ───

    def test_bin_upload_no_asset(self) -> None:
        """上传 .bin 文件不产生 asset 记录。"""
        self.login_admin()
        self._make_upload("data.bin")
        self.assertEqual(self._count_assets(), 0)

    def test_unknown_suffix_no_asset(self) -> None:
        """未知后缀（被规范化为 .bin）不产生 asset 记录。"""
        self.login_admin()
        self._make_upload("file.xyz")
        self.assertEqual(self._count_assets(), 0)

    # ── 4. Asset record has correct fields ────────────

    def test_asset_fields_correct(self) -> None:
        """asset 记录包含正确的必填字段和可选字段 NULL。"""
        self.login_admin()
        resp_json = self._make_upload("cat.png")
        upload_filename = resp_json["data"][0]["filename"]
        asset = self._read_asset_by_relative_path(upload_filename)
        self.assertIsNotNone(asset)
        # 必填字段
        self.assertEqual(asset["storage_area"], "upload")
        self.assertEqual(asset["source"], "upload")
        self.assertEqual(asset["media_type"], "image")
        self.assertEqual(asset["filename"], upload_filename)
        # 可选字段默认为 NULL
        self.assertIsNone(asset["prompt"])
        self.assertIsNone(asset["model"])
        self.assertIsNone(asset["provider"])
        self.assertIsNone(asset["duration_ms"])

    # ── 5. Asset media_type matches suffix ────────────

    def test_image_media_type(self) -> None:
        """图片后缀 → media_type = 'image'。"""
        self.login_admin()
        resp_json = self._make_upload("test.png")
        upload_filename = resp_json["data"][0]["filename"]
        asset = self._read_asset_by_relative_path(upload_filename)
        self.assertEqual(asset["media_type"], "image")

    def test_video_media_type(self) -> None:
        """视频后缀 → media_type = 'video'。"""
        self.login_admin()
        resp_json = self._make_upload("test.mp4")
        upload_filename = resp_json["data"][0]["filename"]
        asset = self._read_asset_by_relative_path(upload_filename)
        self.assertEqual(asset["media_type"], "video")

    # ── 6. Multiple file upload creates multiple assets ──

    def test_multiple_files_create_assets(self) -> None:
        """多文件上传，每个 image/video 文件各产生一条 asset。"""
        self.login_admin()
        resp = self.client.post(
            "/v1/uploads",
            files=[
                ("files", ("a.png", io.BytesIO(b"aaa"), "image/png")),
                ("files", ("b.mp4", io.BytesIO(b"bbb"), "video/mp4")),
                ("files", ("c.bin", io.BytesIO(b"ccc"), "application/octet-stream")),
            ],
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        # 2 个 asset（png + mp4），bin 不产生
        self.assertEqual(self._count_assets(), 2)

    # ── 7. Mixed files create correct asset records ───

    def test_mixed_files_correct_media_types(self) -> None:
        """混合上传时，image 和 video 各自 media_type 正确。"""
        self.login_admin()
        resp = self.client.post(
            "/v1/uploads",
            files=[
                ("files", ("pic.webp", io.BytesIO(b"img"), "image/webp")),
                ("files", ("vid.webm", io.BytesIO(b"vid"), "video/webm")),
                ("files", ("doc.bin", io.BytesIO(b"doc"), "application/octet-stream")),
            ],
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        for item in data:
            asset = self._read_asset_by_relative_path(item["filename"])
            if item["filename"].endswith(".webp"):
                self.assertIsNotNone(asset)
                self.assertEqual(asset["media_type"], "image")
            elif item["filename"].endswith(".webm"):
                self.assertIsNotNone(asset)
                self.assertEqual(asset["media_type"], "video")
            else:
                self.assertIsNone(asset)

    # ── 8. Asset record has correct size ──────────────

    def test_asset_size_matches_file(self) -> None:
        """asset.size 等于上传文件的实际字节数。"""
        self.login_admin()
        content = b"x" * 1024
        resp_json = self._make_upload("sized.png", content)
        upload_filename = resp_json["data"][0]["filename"]
        asset = self._read_asset_by_relative_path(upload_filename)
        self.assertIsNotNone(asset)
        self.assertEqual(asset["size"], 1024)

    # ── 9. Asset record has correct url_path ──────────

    def test_asset_url_path(self) -> None:
        """asset.url_path 是服务内路径 /uploads/{filename}，不含 PUBLIC_BASE_URL。"""
        self.login_admin()
        resp_json = self._make_upload("urltest.jpg")
        upload_filename = resp_json["data"][0]["filename"]
        asset = self._read_asset_by_relative_path(upload_filename)
        self.assertIsNotNone(asset)
        self.assertEqual(asset["url_path"], f"/uploads/{upload_filename}")

    # ── 10. Asset relative_path equals filename ───────

    def test_asset_relative_path_equals_filename(self) -> None:
        """asset.relative_path 等于存储文件名。"""
        self.login_admin()
        resp_json = self._make_upload("rptest.gif")
        upload_filename = resp_json["data"][0]["filename"]
        asset = self._read_asset_by_relative_path(upload_filename)
        self.assertIsNotNone(asset)
        self.assertEqual(asset["relative_path"], upload_filename)

    # ── 11. Upload response structure unchanged ───────

    def test_upload_response_unchanged(self) -> None:
        """上传响应结构不变，仍为 {data: [...]} 包含 uploads 字段。"""
        self.login_admin()
        resp_json = self._make_upload("resp.png")
        self.assertIn("data", resp_json)
        item = resp_json["data"][0]
        expected_keys = {
            "id", "filename", "original_filename", "role",
            "content_type", "url", "local_path", "created_at",
        }
        self.assertTrue(
            expected_keys.issubset(set(item.keys())),
            f"缺少字段: {expected_keys - set(item.keys())}",
        )

    # ── 12. save_asset 幂等性 ─────────────────────────

    def test_save_asset_idempotent_no_extra_row(self) -> None:
        """直接调用 save_asset：相同 (storage_area, relative_path) 不产生额外行。

        注意：此测试验证 save_asset 函数的幂等性，而非上传路由行为。
        上传路由每次生成随机文件名，自然不会产生重复 (relative_path)，
        因此通过路由无法触发 ON CONFLICT 分支。
        """
        from angemedia_gateway.state import save_asset
        save_asset(
            id="idem-001",
            filename="first.png",
            storage_area="upload",
            relative_path="idem.png",
            url_path="/uploads/idem.png",
            media_type="image",
            source="upload",
            size=100,
        )
        self.assertEqual(self._count_assets(), 1)
        save_asset(
            id="idem-002",
            filename="second.png",
            storage_area="upload",
            relative_path="idem.png",
            url_path="/uploads/idem.png",
            media_type="image",
            source="upload",
            size=200,
        )
        # 仍然只有 1 条记录
        self.assertEqual(self._count_assets(), 1)

    def test_save_asset_idempotent_preserves_original_id(self) -> None:
        """直接调用 save_asset：冲突时保留原 id，更新其他字段。

        注意：此测试验证 save_asset 函数的幂等性，而非上传路由行为。
        """
        from angemedia_gateway.state import save_asset
        save_asset(
            id="original-id",
            filename="original.png",
            storage_area="upload",
            relative_path="idem2.png",
            url_path="/uploads/idem2.png",
            media_type="image",
            source="upload",
            size=100,
        )
        save_asset(
            id="different-id",
            filename="updated.png",
            storage_area="upload",
            relative_path="idem2.png",
            url_path="/uploads/idem2.png",
            media_type="image",
            source="upload",
            size=200,
        )
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM assets WHERE storage_area = 'upload' AND relative_path = 'idem2.png'"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(dict(row)["id"], "original-id")
            self.assertEqual(dict(row)["filename"], "updated.png")
            self.assertEqual(dict(row)["size"], 200)
            count = conn.execute(
                "SELECT COUNT(*) FROM assets WHERE relative_path = 'idem2.png'"
            ).fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn.close()
