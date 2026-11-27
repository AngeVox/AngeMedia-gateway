"""分层操作的真实确认、受控 HTTP、恢复和成组入库回归。"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import struct
from threading import Event
import unittest
from unittest.mock import patch
import zlib

import httpx
from fastapi import HTTPException

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway import config as C
from angemedia_gateway.assistant_action_contract import ActionError
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.layer_paths import LAYER_NAMESPACE
from angemedia_gateway.layered_images import LAYER_MODEL, LayeredImageRequest
from angemedia_gateway.reference_images import UnsafeImageReference, local_asset_to_data_url, validate_gateway_image_reference
from angemedia_gateway.repositories.assets import delete_asset, list_assets
from angemedia_gateway.repositories.image_layers import layer_manifest, layer_receipt
from angemedia_gateway.repositories.jobs import cleanup_jobs, get_job
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.routes.jobs import delete_layer_group, get_job_endpoint, get_layer_manifest
from angemedia_gateway.routes.storage import get_asset_detail, get_assets, serve_generated_file
from angemedia_gateway.schemas import ImageRequest
from angemedia_gateway.services.assistant_action_specs import ImageProposalArgs
from angemedia_gateway.services.job_stage_registry import JobStageRegistry, default_job_stage_registry
from angemedia_gateway.services.job_admission import JobAdmissionService
from angemedia_gateway.services.layer_image_admission import prepare_layer_job
from angemedia_gateway.services.layer_image_worker import LayerImageWorker, create_job_dispatch
from angemedia_gateway.services.worker_runtime import WorkerRuntime


def png(width, height, *, alpha=True):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    pixel = b"\x28\x80\xAA\x60" if alpha else b"\x28\x80\xAA"
    rows = (b"\0" + pixel * width) * height
    header = struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


URLS = [f"https://8.8.8.8/layer-{index}?signature=fixture-private" for index in range(3)]
DATA = [png(8, 8), png(2, 2), png(3, 3)]
BOX = {"absolute": [0, 0, 8, 8], "normalized": [0, 0, 1000, 1000]}


def upstream():
    return {"data": [
        {"z_index": 2, "url": URLS[2], "size": "3x3", "bounding_box": BOX, "name": "标签",
         "text_recognition": {"text": "文字", "lines": [
             {"bounding_box": BOX, "runs": [{"text": "文字", "style": {"bold": True, "raw": "discard"}},
                                             {"text": "样式"}]},
         ]}},
        {"z_index": 0, "url": URLS[0], "size": "8x8", "output_format": "png"},
        {"z_index": 1, "url": URLS[1], "size": "2x2", "bounding_box": BOX},
    ]}


class Interrupted(BaseException):
    pass


def killed_worker(root, message):
    """让实际子进程在上游 POST 或第二个成员下载时强制退出。"""
    C.DB_FILE = Path(root) / "state.db"
    C.OUTPUT_DIR = Path(root) / "generated"
    C.UPLOAD_DIR = Path(root) / "uploads"

    def respond(request):
        if request.method == "POST" or str(request.url) == URLS[1]:
            os._exit(85)
        assert str(request.url) == URLS[0]
        return httpx.Response(200, content=DATA[0], headers={"content-type": "image/png"})

    transport = httpx.MockTransport(respond)

    @asynccontextmanager
    async def provider(**_kwargs):
        async with httpx.AsyncClient(transport=transport) as client:
            yield client

    with patch("angemedia_gateway.providers.image.bytedance.provider_client", new=provider), patch(
        "angemedia_gateway.media._remote_media_http_client", new=lambda: httpx.AsyncClient(transport=transport),
    ):
        runtime = WorkerRuntime(registry=default_job_stage_registry(), runtime_refresher=lambda: None)
        runtime.handle(message)


class LayerImagesTest(ActionWorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        update_provider_runtime_config(
            "bytedance", enabled=True, api_key="fixture-layer-key",
            base_url_override="https://ark.example.test/api/v3",
        )
        (self.uploads / "source.png").write_bytes(png(512, 512))
        self.clock = datetime.now(timezone.utc)
        self.worker = LayerImageWorker(clock=lambda: self.clock)
        self.runtime = WorkerRuntime(
            registry=JobStageRegistry({"image_generate": self.worker.handle_generate,
                                       "asset_import": self.worker.handle_import}),
            runtime_refresher=lambda: None,
        )

    def request(self, **changes):
        return {"operation": "decompose", "model": "seedream-5-5", "image": "/uploads/source.png",
                "prompt": "分离物体与文字", "max_images": 3, "text_recognition": True} | changes

    def receipt(self, job_id):
        with closing(db_connect()) as conn:
            return layer_receipt(conn, job_id)

    def counts_for(self, job_id):
        with closing(db_connect()) as conn:
            return tuple(conn.execute(f"SELECT count(*) FROM {table} WHERE job_id=?", (job_id,)).fetchone()[0]
                         for table in ("assets", "generations"))

    def clients(self, *, result=None, before_post=None, on_download=None):
        calls = []

        def respond(request):
            if request.method == "POST":
                calls.append(("POST", json.loads(request.content)))
                self.assertEqual(request.headers["authorization"], "Bearer fixture-layer-key")
                if before_post:
                    before_post()
                return httpx.Response(200, json=upstream() if result is None else result)
            self.assertNotIn("authorization", request.headers)
            index = URLS.index(str(request.url))
            calls.append(("GET", index))
            if on_download:
                response = on_download(index)
                if response is not None:
                    return response
            return httpx.Response(200, content=DATA[index], headers={"content-type": "image/png"})

        transport = httpx.MockTransport(respond)

        @asynccontextmanager
        async def provider(**_kwargs):
            async with httpx.AsyncClient(transport=transport) as client:
                yield client

        stack = ExitStack()
        stack.enter_context(patch("angemedia_gateway.providers.image.bytedance.provider_client", new=provider))
        stack.enter_context(patch("angemedia_gateway.media._remote_media_http_client",
                                  new=lambda: httpx.AsyncClient(transport=transport)))
        return stack, calls

    def step(self, job_id):
        return self.runtime.handle(self.message(job_id))["status"]

    def test_strict_request_reuses_fee_confirmation_and_keeps_normal_api_isolated(self):
        request = self.request()
        self.assertEqual(ImageProposalArgs.model_validate({"request": request}).request.operation, "decompose")
        with self.assertRaises(ValueError):
            ImageRequest.model_validate(request)
        for changes in ({"max_images": True}, {"max_images": 101}, {"image": "https://example.invalid/raw.png"},
                        {"reference_images": ["/uploads/source.png"]}, {"output_format": "webp"}, {"size": "512x512"}):
            with self.subTest(changes=changes):
                self.assert_code("invalid_action_request", self.propose, request=self.request(**changes))
        action = self.propose(request=request)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(action["review"]["required_acknowledgements"], ["may_incur_cost"])
        self.assert_code("action_acknowledgements_required", self.service.confirm, action["id"],
                         self.confirmation(action, acknowledgements=[]), access=self.alice)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_download_retry_preserves_members_and_publishes_only_complete_group(self):
        job_id = self.job(self.propose(request=self.request()))["id"]
        submit = self.message(job_id)
        attempts = 0

        def failure(index):
            nonlocal attempts
            if index == 1:
                attempts += 1
                if attempts == 1:
                    return httpx.Response(503)

        stack, calls = self.clients(on_download=failure)
        with stack:
            self.assertEqual(self.step(job_id), "scheduled")
            self.assertEqual(self.runtime.handle(submit)["status"], "duplicate")
            self.assertEqual(self.step(job_id), "scheduled")
            before = self.receipt(job_id)
            self.assertEqual(before["state"], "received")
            self.assertIn("fixture-private", before["result_json"])
            self.assertEqual(self.counts_for(job_id), (0, 0))
            self.assertIsNone(layer_manifest(job_id))
            name = f"{LAYER_NAMESPACE}/{job_id}/000.png"
            reference = "/generated/" + name
            self.assertTrue((self.output / name).exists())
            self.assertIsNone(local_asset_to_data_url(reference))
            with self.assertRaises(UnsafeImageReference):
                validate_gateway_image_reference(reference)
            with self.assertRaises(HTTPException) as error:
                asyncio.run(serve_generated_file(name))
            self.assertEqual(error.exception.status_code, 404)
            self.clock += timedelta(seconds=5)
            self.assertEqual(self.step(job_id), "succeeded")
        self.assertEqual([call[0] for call in calls], ["POST", "GET", "GET", "GET", "GET"])
        self.assertEqual(calls[1:], [("GET", 0), ("GET", 1), ("GET", 1), ("GET", 2)])
        payload = calls[0][1]
        self.assertEqual(payload["model"], LAYER_MODEL)
        self.assertEqual(payload["layer_decomposition_options"], {"max_images": 3, "text_recognition": True})
        self.assertTrue(payload["image"].startswith("data:image/png;base64,"))
        manifest = get_layer_manifest(job_id)["data"]
        self.assertEqual([entry["z_index"] for entry in manifest["members"]], [0, 1, 2])
        self.assertEqual(manifest["members"][2]["text_recognition"]["text"], "文字")
        self.assertNotIn("fixture-private", json.dumps(manifest))
        self.assertIsNone(self.receipt(job_id)["result_json"])
        self.assertEqual(self.counts_for(job_id), (3, 1))
        for member, data in zip(manifest["members"], DATA, strict=True):
            self.assertEqual((self.output / member["url"].removeprefix("/generated/")).read_bytes(), data)
        self.assertIsNotNone(local_asset_to_data_url(reference))
        self.assertEqual(validate_gateway_image_reference(reference), reference)
        with self.assertRaises(HTTPException) as error:
            delete_asset(manifest["members"][0]["asset_id"])
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(error.exception.detail, {"code": "layer_group_requires_group_deletion"})
        self.assertEqual(self.counts_for(job_id), (3, 1))

    def test_concurrent_submit_has_one_post(self):
        job_id = self.job(self.propose(request=self.request()))["id"]
        message = self.message(job_id)
        started, release = Event(), Event()

        def wait():
            started.set()
            assert release.wait(10)

        stack, calls = self.clients(before_post=wait)
        with stack, ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.runtime.handle, message)
            try:
                self.assertTrue(started.wait(10))
                self.assertEqual(pool.submit(self.runtime.handle, message).result(10)["status"], "inflight")
            finally:
                release.set()
            self.assertEqual(first.result(10)["status"], "scheduled")
        self.assertEqual([call[0] for call in calls], ["POST"])

    def test_interruption_after_post_without_receipt_marks_unknown_without_resubmitting(self):
        job_id = self.job(self.propose(request=self.request()))["id"]
        message = self.message(job_id)

        def stop():
            raise Interrupted()

        stack, calls = self.clients(before_post=stop)
        with stack:
            with self.assertRaises(Interrupted):
                self.runtime.handle(message)
            self.assertEqual(self.receipt(job_id)["state"], "prepared")
            self.assertEqual(self.runtime.handle(message)["status"], "inflight")
            self.clock = datetime.fromisoformat(self.receipt(job_id)["deadline"])
            self.assertEqual(self.runtime.handle(message)["status"], "failed")
        self.assertEqual([call[0] for call in calls], ["POST"])
        self.assertEqual(get_job(job_id)["error_code"], "image_layer_submit_unknown")

    def test_import_restart_keeps_downloaded_members_and_expires_without_recreating_job(self):
        job_id = self.job(self.propose(request=self.request()))["id"]

        def stop(index):
            if index == 1:
                raise Interrupted()

        stack, calls = self.clients(on_download=stop)
        with stack:
            self.assertEqual(self.step(job_id), "scheduled")
            message = self.message(job_id)
            with self.assertRaises(Interrupted):
                self.runtime.handle(message)
            self.assertEqual(self.runtime.handle(message)["status"], "inflight")
            self.clock = datetime.fromisoformat(self.receipt(job_id)["deadline"])
            self.assertEqual(self.runtime.handle(message)["status"], "failed")
        self.assertEqual(get_job(job_id)["error_code"], "image_layer_links_expired")
        self.assertEqual(self.counts_for(job_id), (0, 0))
        self.assertIsNone(self.receipt(job_id)["result_json"])
        self.assertEqual(calls[1:], [("GET", 0), ("GET", 1)])

    def test_failed_outbox_handoff_keeps_receipt_and_reuses_original_submit_guard(self):
        job_id = self.job(self.propose(request=self.request()))["id"]
        message = self.message(job_id)

        def no_import(**kwargs):
            if kwargs["payload"]["stage"] == "asset_import":
                raise sqlite3.OperationalError("fixture outbox failure")
            return create_job_dispatch(**kwargs)

        stack, calls = self.clients()
        with stack:
            with patch("angemedia_gateway.services.layer_image_worker.create_job_dispatch", side_effect=no_import):
                self.assertEqual(self.runtime.handle(message)["status"], "receipt_saved")
            self.assertEqual(self.receipt(job_id)["state"], "received")
            self.assertEqual(self.runtime.handle(message)["status"], "scheduled")
            self.assertEqual(self.step(job_id), "succeeded")
        self.assertEqual([call[0] for call in calls].count("POST"), 1)

    def test_publish_failure_rolls_back_every_asset_and_reuses_downloads(self):
        job_id = self.job(self.propose(request=self.request()))["id"]
        stack, calls = self.clients()
        with stack:
            self.assertEqual(self.step(job_id), "scheduled")
            with patch("angemedia_gateway.services.layer_image_worker.record_generation",
                       side_effect=sqlite3.OperationalError("fixture history failure")):
                self.assertEqual(self.step(job_id), "scheduled")
            self.assertEqual(self.counts_for(job_id), (0, 0))
            self.assertEqual(self.receipt(job_id)["state"], "received")
            self.assertIsNone(layer_manifest(job_id))
            self.clock += timedelta(seconds=5)
            self.assertEqual(self.step(job_id), "succeeded")
        self.assertEqual(calls[1:], [("GET", 0), ("GET", 1), ("GET", 2)])
        self.assertEqual(self.counts_for(job_id), (3, 1))

    def test_invalid_response_fails_before_any_download(self):
        result = upstream()
        result["data"][0]["z_index"] = 0
        job_id = self.job(self.propose(request=self.request()))["id"]
        stack, calls = self.clients(result=result)
        with stack:
            self.assertEqual(self.step(job_id), "failed")
        self.assertEqual(get_job(job_id)["error_code"], "image_layer_result_invalid")
        self.assertEqual(self.counts_for(job_id), (0, 0))
        self.assertEqual([call[0] for call in calls], ["POST"])

    def test_response_budget_and_opaque_layer_do_not_publish_partial_results(self):
        job_id = self.job(self.propose(request=self.request()))["id"]
        stack, calls = self.clients()
        with stack, patch("angemedia_gateway.providers.image.bytedance._LAYER_RESPONSE_MAX_BYTES", 64):
            self.assertEqual(self.step(job_id), "failed")
        self.assertEqual([call[0] for call in calls], ["POST"])
        self.assertEqual(get_job(job_id)["error_code"], "image_layer_result_invalid")

        def opaque(index):
            if index == 1:
                return httpx.Response(200, content=png(2, 2, alpha=False))

        job_id = self.job(self.propose(request=self.request(prompt="错误图层夹具")))["id"]
        stack, calls = self.clients(on_download=opaque)
        with stack:
            self.assertEqual(self.step(job_id), "scheduled")
            self.assertEqual(self.step(job_id), "failed")
        self.assertEqual(calls[1:], [("GET", 0), ("GET", 1)])
        self.assertEqual(get_job(job_id)["error_code"], "image_layer_artifact_invalid")
        self.assertEqual(self.counts_for(job_id), (0, 0))
        self.assertFalse((self.output / LAYER_NAMESPACE / job_id).exists())

    def test_worker_normal_registry_requires_confirmed_grant_and_routes_both_stages(self):
        job_id = self.job(self.propose(request=self.request()))["id"]
        self.runtime = WorkerRuntime(registry=default_job_stage_registry(), runtime_refresher=lambda: None)
        stack, calls = self.clients()
        with stack:
            unconfirmed = JobAdmissionService().admit(
                **prepare_layer_job(LayeredImageRequest.model_validate(self.request())).arguments()
            ).job["id"]
            self.assertEqual(self.step(unconfirmed), "failed")
            self.assertEqual(calls, [])
            self.assertEqual(get_job(unconfirmed)["error_code"], "action_execution_grant_missing")
            self.assertEqual(self.step(job_id), "scheduled")
            self.assertEqual(self.step(job_id), "succeeded")
        self.assertEqual([call[0] for call in calls], ["POST", "GET", "GET", "GET"])
        self.assertEqual(len(list_assets(job_id=job_id)), 3)

    def test_process_exit_and_cold_worker_recovery(self):
        def kill_at(message):
            process = multiprocessing.get_context("spawn").Process(
                target=killed_worker, args=(str(self.temp), message),
            )
            try:
                process.start()
                process.join(20)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                    self.fail("分层故障进程未按时退出")
                self.assertEqual(process.exitcode, 85)
            finally:
                process.close()

        submit_id = self.job(self.propose(request=self.request()))["id"]
        submit = self.message(submit_id)
        kill_at(submit)
        self.clock = datetime.fromisoformat(self.receipt(submit_id)["deadline"])
        stack, calls = self.clients()
        with stack:
            self.assertEqual(self.runtime.handle(submit)["status"], "failed")
        self.assertEqual(calls, [])
        self.assertEqual(get_job(submit_id)["error_code"], "image_layer_submit_unknown")

        self.clock = datetime.now(timezone.utc)
        job_id = self.job(self.propose(request=self.request(prompt="中断取回")))["id"]
        stack, calls = self.clients()
        with stack:
            self.assertEqual(self.step(job_id), "scheduled")
        self.assertEqual([call[0] for call in calls], ["POST"])
        message = self.message(job_id)
        kill_at(message)
        self.assertEqual(self.counts_for(job_id), (0, 0))
        self.assertTrue((self.output / LAYER_NAMESPACE / job_id / "000.png").is_file())
        self.clock = datetime.fromisoformat(get_job(job_id)["claim_expires_at"])
        stack, calls = self.clients()
        with stack:
            self.assertEqual(self.runtime.handle(message)["status"], "succeeded")
        self.assertEqual(calls, [("GET", 1), ("GET", 2)])
        self.assertEqual(self.counts_for(job_id), (3, 1))

    def test_group_remains_readable_after_job_cleanup_and_deletes_as_one_unit(self):
        job_id = self.job(self.propose(request=self.request()))["id"]
        detail = asyncio.run(get_job_endpoint(job_id))["data"]
        self.assertEqual(detail["operation_kind"], "image_layer_decomposition")
        self.assertEqual(detail["assets"], [])
        stack, _calls = self.clients()
        with stack:
            self.assertEqual(self.step(job_id), "scheduled")
            self.assertEqual(self.step(job_id), "succeeded")
        name = f"{LAYER_NAMESPACE}/{job_id}/000.png"
        file = asyncio.run(serve_generated_file(name))
        self.assertEqual(file.headers["Cache-Control"], "private, no-store")
        cleanup_jobs(job_ids=[job_id], statuses=["succeeded"])
        self.assertIsNone(get_job(job_id))
        self.assertIsNotNone(local_asset_to_data_url("/generated/" + name))
        members = get_layer_manifest(job_id)["data"]["members"]
        assets = get_assets(limit=20, offset=0, job_id=None, media_type=None, source=None)["data"]
        summaries = {asset["id"]: asset for asset in assets}
        for member in members:
            self.assertEqual(summaries[member["asset_id"]]["layer_group"],
                             {"job_id": job_id, "z_index": member["z_index"]})
            self.assertIsNone(summaries[member["asset_id"]]["job_id"])
        self.assertEqual(asyncio.run(get_asset_detail(members[0]["asset_id"]))["data"]["layer_group"],
                         {"job_id": job_id, "z_index": 0})
        self.assertTrue(delete_layer_group(job_id)["ok"])
        self.assertTrue(delete_layer_group(job_id)["ok"])
        self.assertFalse((self.output / name).exists())
        assets = get_assets(limit=20, offset=0, job_id=None, media_type=None, source=None)["data"]
        remaining = {asset["id"] for asset in assets}
        self.assertTrue(remaining.isdisjoint(member["asset_id"] for member in members))
        self.assertIsNone(layer_manifest(job_id))
        self.assertEqual(self.receipt(job_id)["state"], "removed")
        # 清理失败留下的文件也不能使已撤下的图层重新公开。
        remaining_file = self.output / name
        remaining_file.parent.mkdir(parents=True, exist_ok=True)
        remaining_file.write_bytes(DATA[0])
        reference = "/generated/" + name
        self.assertIsNone(local_asset_to_data_url(reference))
        with self.assertRaises(UnsafeImageReference):
            validate_gateway_image_reference(reference)
        with self.assertRaises(HTTPException) as error:
            asyncio.run(serve_generated_file(name))
        self.assertEqual(error.exception.status_code, 404)
