"""生成成功后写入 assets 表的集成测试。"""
from __future__ import annotations

import asyncio
import copy
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.schemas import ImageRequest, VideoRequest  # noqa: E402
from angemedia_gateway.services import media_service as media_mod  # noqa: E402
from angemedia_gateway.services.media_service import MediaService  # noqa: E402
from angemedia_gateway.state import init_db, list_assets  # noqa: E402


class FakeImageProvider:
    def __init__(self, result: dict) -> None:
        self.result = result

    async def generate(self, req: ImageRequest, target: object) -> dict:
        return copy.deepcopy(self.result)


class FakeAgnesVideo:
    def __init__(self, result: dict) -> None:
        self.result = result

    async def generate_video(self, req: VideoRequest) -> dict:
        return copy.deepcopy(self.result)


class AssetsGenerationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="assets-generation-test-")
        self.db_path = Path(self._tmp_dir) / "test.db"
        self.output_dir = Path(self._tmp_dir) / "generated"
        self.upload_dir = Path(self._tmp_dir) / "uploads"
        self.output_dir.mkdir()
        self.upload_dir.mkdir()

        self._orig_db = C.DB_FILE
        self._orig_output = C.OUTPUT_DIR
        self._orig_upload = C.UPLOAD_DIR
        self._orig_public_base_url = C.PUBLIC_BASE_URL
        self._orig_auto_download = C.AUTO_DOWNLOAD_GENERATED

        C.DB_FILE = self.db_path
        C.OUTPUT_DIR = self.output_dir
        C.UPLOAD_DIR = self.upload_dir
        C.PUBLIC_BASE_URL = "http://testserver"
        C.AUTO_DOWNLOAD_GENERATED = True
        init_db()

    def tearDown(self) -> None:
        C.DB_FILE = self._orig_db
        C.OUTPUT_DIR = self._orig_output
        C.UPLOAD_DIR = self._orig_upload
        C.PUBLIC_BASE_URL = self._orig_public_base_url
        C.AUTO_DOWNLOAD_GENERATED = self._orig_auto_download
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _generated_url(self, filename: str) -> str:
        return f"{C.PUBLIC_BASE_URL}/generated/{filename}"

    def _run_builtin_image(self, result: dict, prompt: str = "a generated cat") -> dict:
        fake_target = SimpleNamespace(provider="fake", model="fake-model")
        fake_provider = FakeImageProvider(result)
        with (
            patch("angemedia_gateway.services.media_service.resolve_chain", return_value=[fake_target]),
            patch.dict(media_mod.PROVIDERS, {"fake": fake_provider}, clear=True),
        ):
            return asyncio.run(MediaService().create_image(ImageRequest(prompt=prompt, model="fake")))

    def _asset_rows_for_path(self, relative_path: str) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM assets WHERE storage_area = 'output' AND relative_path = ?",
                (relative_path,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def test_image_generation_with_local_path_writes_asset_and_preserves_response(self) -> None:
        filename = "generated-image.png"
        image_file = self.output_dir / filename
        image_file.write_bytes(b"fake image bytes")
        result = {
            "created": 0,
            "data": [{
                "url": self._generated_url(filename),
                "local_path": str(image_file),
                "localized": True,
            }],
        }

        response = self._run_builtin_image(result, prompt="a generated cat")

        assets = list_assets(limit=10)
        self.assertEqual(len(assets), 1)
        asset = assets[0]
        self.assertEqual(asset["source"], "generated")
        self.assertEqual(asset["media_type"], "image")
        self.assertEqual(asset["storage_area"], "output")
        self.assertEqual(asset["relative_path"], filename)
        self.assertEqual(asset["url_path"], f"/generated/{filename}")
        self.assertEqual(asset["size"], image_file.stat().st_size)
        self.assertEqual(asset["prompt"], "a generated cat")
        self.assertEqual(asset["model"], "fake-model")
        self.assertEqual(asset["provider"], "fake")
        self.assertEqual(asset["duration_ms"], response["duration_ms"])

        self.assertIn("data", response)
        self.assertEqual(response["data"][0]["url"], self._generated_url(filename))
        self.assertEqual(response["data"][0]["local_path"], str(image_file))
        self.assertIn("history_id", response)
        self.assertNotIn("asset", response)
        self.assertNotIn("asset_id", response)

    def test_image_generation_with_multiple_local_paths_writes_each_valid_asset(self) -> None:
        first_name = "multi-first.png"
        second_name = "multi-second.png"
        missing_name = "multi-missing.png"
        outside_name = "multi-outside.png"
        first_file = self.output_dir / first_name
        second_file = self.output_dir / second_name
        missing_file = self.output_dir / missing_name
        outside_file = Path(self._tmp_dir) / outside_name
        first_file.write_bytes(b"first image bytes")
        second_file.write_bytes(b"second image bytes")
        outside_file.write_bytes(b"outside image bytes")
        result = {
            "created": 0,
            "data": [
                {
                    "url": self._generated_url(first_name),
                    "local_path": str(first_file),
                    "localized": True,
                },
                {
                    "url": self._generated_url("no-local-path.png"),
                    "localized": True,
                },
                {
                    "url": self._generated_url(missing_name),
                    "local_path": str(missing_file),
                    "localized": True,
                },
                {
                    "url": self._generated_url(outside_name),
                    "local_path": str(outside_file),
                    "localized": True,
                },
                {
                    "url": self._generated_url(second_name),
                    "local_path": str(second_file),
                    "localized": True,
                },
            ],
        }

        response = self._run_builtin_image(result, prompt="multi image prompt")

        assets = {asset["relative_path"]: asset for asset in list_assets(limit=10)}
        self.assertEqual(set(assets), {first_name, second_name})
        self.assertEqual(assets[first_name]["source"], "generated")
        self.assertEqual(assets[first_name]["media_type"], "image")
        self.assertEqual(assets[first_name]["storage_area"], "output")
        self.assertEqual(assets[first_name]["url_path"], f"/generated/{first_name}")
        self.assertEqual(assets[first_name]["size"], first_file.stat().st_size)
        self.assertEqual(assets[first_name]["prompt"], "multi image prompt")
        self.assertEqual(assets[first_name]["model"], "fake-model")
        self.assertEqual(assets[first_name]["provider"], "fake")
        self.assertEqual(assets[first_name]["duration_ms"], response["duration_ms"])
        self.assertEqual(assets[second_name]["url_path"], f"/generated/{second_name}")
        self.assertEqual(assets[second_name]["size"], second_file.stat().st_size)

        self.assertEqual(len(response["data"]), 5)
        self.assertEqual(response["data"][0]["local_path"], str(first_file))
        self.assertNotIn("local_path", response["data"][1])
        self.assertEqual(response["data"][2]["local_path"], str(missing_file))
        self.assertEqual(response["data"][3]["local_path"], str(outside_file))
        self.assertEqual(response["data"][4]["local_path"], str(second_file))
        self.assertNotIn("asset", response)
        self.assertNotIn("asset_id", response)

    def test_image_generation_without_local_path_does_not_write_asset(self) -> None:
        result = {"created": 0, "data": [{"url": self._generated_url("no-local-path.png")}]}

        response = self._run_builtin_image(result)

        self.assertEqual(list_assets(limit=10), [])
        self.assertIn("data", response)
        self.assertNotIn("local_path", response["data"][0])

    def test_remote_image_generation_forces_localization_for_preview_and_assets(self) -> None:
        C.AUTO_DOWNLOAD_GENERATED = False
        filename = "forced-localized.png"
        image_file = self.output_dir / filename
        remote_url = "https://cdn.example.com/rendered/kolors.png"
        result = {"created": 0, "data": [{"url": remote_url}]}
        seen: dict[str, object] = {}

        async def fake_try_download_remote_media(
            url: str,
            prefix: str,
            fallback_ext: str,
            stable_id: str | None = None,
            *,
            force: bool = False,
        ):
            seen["url"] = url
            seen["prefix"] = prefix
            seen["fallback_ext"] = fallback_ext
            seen["stable_id"] = stable_id
            seen["force"] = force
            image_file.write_bytes(b"localized image bytes")
            return self._generated_url(filename), str(image_file), None

        with patch("angemedia_gateway.media.try_download_remote_media", fake_try_download_remote_media):
            response = self._run_builtin_image(result, prompt="localized cat")

        self.assertEqual(seen["url"], remote_url)
        self.assertEqual(seen["prefix"], "image_fake")
        self.assertEqual(seen["fallback_ext"], ".png")
        self.assertTrue(seen["force"])
        self.assertIn("fake:fake-model", str(seen["stable_id"]))
        self.assertEqual(response["data"][0]["remote_url"], remote_url)
        self.assertEqual(response["data"][0]["url"], self._generated_url(filename))
        self.assertEqual(response["data"][0]["local_path"], str(image_file))
        self.assertTrue(response["data"][0]["localized"])

        assets = list_assets(limit=10)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["source"], "generated")
        self.assertEqual(assets[0]["media_type"], "image")
        self.assertEqual(assets[0]["url_path"], f"/generated/{filename}")
        self.assertEqual(assets[0]["prompt"], "localized cat")

    def test_remote_generated_path_is_not_mistaken_for_gateway_local_file(self) -> None:
        from angemedia_gateway.media import is_generated_local_url

        self.assertTrue(is_generated_local_url("/generated/local.png"))
        self.assertTrue(is_generated_local_url("http://testserver/generated/local.png"))
        self.assertFalse(is_generated_local_url("https://cdn.example.com/generated/remote.png"))

    def test_image_generation_with_missing_local_path_file_does_not_write_asset(self) -> None:
        filename = "missing-image.png"
        missing_path = self.output_dir / filename
        result = {
            "created": 0,
            "data": [{
                "url": self._generated_url(filename),
                "local_path": str(missing_path),
                "localized": True,
            }],
        }

        response = self._run_builtin_image(result)

        self.assertFalse(missing_path.exists())
        self.assertEqual(list_assets(limit=10), [])
        self.assertEqual(response["data"][0]["local_path"], str(missing_path))

    def test_image_generation_with_local_path_outside_output_dir_does_not_write_asset(self) -> None:
        filename = "outside-output.png"
        outside_file = Path(self._tmp_dir) / filename
        outside_file.write_bytes(b"outside output bytes")
        result = {
            "created": 0,
            "data": [{
                "url": self._generated_url(filename),
                "local_path": str(outside_file),
                "localized": True,
            }],
        }

        response = self._run_builtin_image(result)

        self.assertTrue(outside_file.exists())
        self.assertEqual(list_assets(limit=10), [])
        self.assertEqual(response["data"][0]["local_path"], str(outside_file))

    def test_repeated_generation_same_local_path_does_not_duplicate_asset(self) -> None:
        filename = "same-path.png"
        image_file = self.output_dir / filename
        image_file.write_bytes(b"same image bytes")
        result = {
            "created": 0,
            "data": [{
                "url": self._generated_url(filename),
                "local_path": str(image_file),
                "localized": True,
            }],
        }

        self._run_builtin_image(result, prompt="first prompt")
        self._run_builtin_image(result, prompt="second prompt")

        rows = self._asset_rows_for_path(filename)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prompt"], "second prompt")
        self.assertEqual(rows[0]["size"], image_file.stat().st_size)

    def test_video_generation_with_local_path_writes_video_asset(self) -> None:
        filename = "generated-video.mp4"
        video_file = self.output_dir / filename
        video_file.write_bytes(b"fake video bytes")
        video_result = {
            "task_id": "asset-video-task",
            "status": "completed",
            "video_url": self._generated_url(filename),
            "local_path": str(video_file),
            "localized": True,
        }

        with (
            patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True),
            patch("angemedia_gateway.services.media_service.agnes_video", FakeAgnesVideo(video_result)),
        ):
            response = asyncio.run(
                MediaService().create_video(
                    VideoRequest(prompt="a generated video", model="agnes-video-v2.0", wait_for_completion=True)
                )
            )

        assets = list_assets(limit=10)
        self.assertEqual(len(assets), 1)
        asset = assets[0]
        self.assertEqual(asset["source"], "generated")
        self.assertEqual(asset["media_type"], "video")
        self.assertEqual(asset["storage_area"], "output")
        self.assertEqual(asset["relative_path"], filename)
        self.assertEqual(asset["url_path"], f"/generated/{filename}")
        self.assertEqual(asset["size"], video_file.stat().st_size)
        self.assertEqual(asset["prompt"], "a generated video")
        self.assertEqual(asset["model"], "agnes-video-v2.0")
        self.assertEqual(asset["provider"], "agnes_video")
        self.assertEqual(asset["duration_ms"], response["duration_ms"])

        self.assertEqual(response["video_url"], self._generated_url(filename))
        self.assertEqual(response["local_path"], str(video_file))
        self.assertIn("history_id", response)
        self.assertNotIn("asset", response)
        self.assertNotIn("asset_id", response)


if __name__ == "__main__":
    unittest.main()
