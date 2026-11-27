"""Seedream 5.5 单图、透明背景、请求授权和原作品入库。"""
import asyncio
import base64
from contextlib import ExitStack, asynccontextmanager, closing
import json
import unittest
from unittest.mock import patch

import httpx

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.providers.catalog.api import catalog_api_response
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.providers.catalog.validation import CatalogOperationValidationError
from angemedia_gateway.providers.errors import ProviderProtocolError
from angemedia_gateway.providers.image.bytedance import ByteDanceImageProvider, build_bytedance_image_payload
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.routing import resolve_chain
from angemedia_gateway.schemas import ImageRequest
from angemedia_gateway.services.image_execution import ImageExecutionService, InvalidImageRequest, build_image_execution_plan
from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService
from angemedia_gateway.services.image_job_worker import ImageJobWorker
from angemedia_gateway.services.job_stage_registry import JobStageRegistry
from angemedia_gateway.services.worker_runtime import WorkerRuntime
from test_web_studio_generate_image_operation_controls import run_studio_module_script


MODEL = "dola-seedream-5-5-261028"
TARGET = RouteTarget("bytedance", MODEL)
REMOTE_URL = "https://8.8.8.8/seedream-fixture.png"
# 两个像素分别有零和部分透明度。由标准库生成并在测试外核对 RGBA。
RGBA_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAYAAAD0In+KAAAAEUlEQVR4nGP4z6DA4NDAcAIADAkCqFP5fEYAAAAASUVORK5CYII="
RGBA = base64.b64decode(RGBA_B64)


class Seedream55ContractTest(ActionWorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        update_provider_runtime_config(
            "bytedance", enabled=True, api_key="fixture-seedream-55-key",
            base_url_override="https://ark.example.test/api/v3",
        )

    def request(self, **changes):
        values = {"model": "seedream-5-5", "prompt": "透明背景的贴纸", "size": "1.5K"}
        return ImageRequest(**(values | changes))

    def clients(self, items):
        calls = []

        def reply(request):
            calls.append((request.method, str(request.url), json.loads(request.content) if request.content else None))
            if request.method == "POST":
                self.assertEqual(request.headers["authorization"], "Bearer fixture-seedream-55-key")
                return httpx.Response(200, json={"data": items})
            self.assertEqual(str(request.url), REMOTE_URL)
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(200, content=RGBA, headers={"content-type": "image/png"})

        transport = httpx.MockTransport(reply)

        @asynccontextmanager
        async def provider_client(**_kwargs):
            async with httpx.AsyncClient(transport=transport) as client:
                yield client

        stack = ExitStack()
        stack.enter_context(patch("angemedia_gateway.providers.image.bytedance.provider_client", new=provider_client))
        stack.enter_context(patch("angemedia_gateway.media._remote_media_http_client",
                                  new=lambda: httpx.AsyncClient(transport=transport)))
        return stack, calls

    def runtime(self):
        worker = ImageJobWorker(executor=ImageExecutionService(providers={"bytedance": ByteDanceImageProvider()}))
        return WorkerRuntime(registry=JobStageRegistry({"image_generate": worker.handle}), runtime_refresher=lambda: None)

    def test_catalog_resolves_explicit_candidate_and_preserves_old_routes(self):
        for selector in ("seedream-5-5", "seedream-5.5", MODEL, "bytedance::seedream-5-5"):
            with self.subTest(selector=selector):
                self.assertEqual(resolve_chain(selector), [TARGET])
        catalog = load_provider_catalog()
        model = next(item for item in catalog_api_response(catalog)["models"] if item["id"] == "seedream-5-5")
        self.assertEqual(model["status"], "experimental")
        self.assertTrue(model["selectable"])
        self.assertFalse(model["policy"]["generation_verified"])
        self.assertFalse(model["policy"]["account_access_verified"])
        self.assertNotIn("seedream-5-5", [entry.id for entry in catalog.default_image_chain()])
        self.assertEqual(resolve_chain("seedream"), [RouteTarget("bytedance", "seedream-5-0-lite-260128")])
        self.assertFalse(catalog.providers_by_id["bytedance"].enabled_default)
        update_provider_runtime_config("bytedance", enabled=False)
        self.assertEqual(resolve_chain("seedream-5-5"), [])

    def test_both_operations_share_documented_size_and_reference_bounds(self):
        for size in ("1K", "1.5K", "2K", "960x960", "2880x1440", "3840x240"):
            with self.subTest(size=size):
                self.assertEqual(build_bytedance_image_payload(self.request(size=size), TARGET)["size"], size)
        references = [f"https://cdn.example.test/{index}.png" for index in range(11)]
        payload = build_bytedance_image_payload(self.request(
            image=references[0], reference_images=references[1:10], background="transparent",
        ), TARGET)
        self.assertEqual(payload["image"], references[:10])
        self.assertEqual(payload["background"], "transparent")
        self.assertNotIn("output_format", payload)
        for values in ({"reference_images": references}, {"size": "4096x4096"}, {"size": "auto"}, {"size": "8192x128"}):
            with self.subTest(values=values), self.assertRaises(CatalogOperationValidationError):
                build_bytedance_image_payload(self.request(**values), TARGET)

    def test_unsupported_outputs_fail_before_admission_and_provider_http(self):
        stack, calls = self.clients([{"url": REMOTE_URL}])
        with stack:
            for values in (
                {"background": "transparent", "output_format": "jpeg"},
                {"output_format": "webp"},
                {"layer_decomposition": True},
                {"layer_decomposition_options": {"max_images": 12, "text_recognition": True}},
                {"extra_body": {"layer_decomposition": True}},
                {"extra_body": {"layer_decomposition_options": {"max_images": 12}}},
                {"sequential_image_generation": "auto"},
                {"stream": True},
                {"model": "seedream-5-flash", "background": "transparent"},
            ):
                with self.subTest(values=values):
                    request = self.request(**values)
                    with self.assertRaises(InvalidImageRequest):
                        build_image_execution_plan(request)
                    with self.assertRaises((InvalidImageRequest, ValueError)):
                        ImageJobAdmissionService().submit(request)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(calls, [])

    def test_base64_output_returns_original_transparency_and_preserves_api_options(self):
        stack, calls = self.clients([{"b64_json": RGBA_B64, "output_format": "png", "z_index": None, "error": None}])
        req = self.request(response_format="b64_json", background="transparent", watermark=False)
        with stack:
            result = asyncio.run(ByteDanceImageProvider().generate(req, TARGET))
        self.assertEqual(base64.b64decode(result["data"][0]["b64_json"]), RGBA)
        self.assertEqual(calls, [("POST", "https://ark.example.test/api/v3/images/generations", {
            "model": MODEL, "prompt": req.prompt, "size": "1.5K", "response_format": "b64_json",
            "watermark": False, "background": "transparent",
        })])

    def test_confirmed_multireference_edit_delivers_png_bytes_once_through_original_worker(self):
        paths = []
        for index in range(10):
            name = f"reference-{index}.png"
            (self.uploads / name).write_bytes(RGBA)
            paths.append("/uploads/" + name)
        request = self.request(operation="edit", reference_images=paths, background="transparent", output_format="png")
        action = self.propose(request=request.model_dump(exclude_none=True))
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertIn("may_incur_cost", action["review"]["required_acknowledgements"])
        self.assertEqual(action["review"]["request"]["background"], "transparent")
        job = self.job(action)
        message = self.message(job["id"])
        stack, calls = self.clients([{"url": REMOTE_URL, "output_format": "png"}])
        with stack:
            runtime = self.runtime()
            self.assertEqual(runtime.handle(message)["status"], "succeeded")
            self.assertEqual(runtime.handle(message)["status"], "duplicate")
        self.assertEqual([call[0] for call in calls], ["POST", "GET"])
        payload = calls[0][2]
        self.assertEqual((payload["model"], payload["background"], payload["response_format"]), (MODEL, "transparent", "url"))
        self.assertEqual(payload["image"], [f"data:image/png;base64,{RGBA_B64}"] * 10)
        assets = list_assets(job_id=job["id"])
        self.assertEqual(len(assets), 1)
        path = self.output / assets[0]["relative_path"]
        self.assertEqual(path.suffix, ".png")
        self.assertEqual(path.read_bytes(), RGBA)
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM generations WHERE job_id=?", (job["id"],)).fetchone()[0], 1)

    def test_unexpected_layer_outputs_fail_without_partial_asset_or_history(self):
        for items in (
            [{"url": REMOTE_URL}, {"b64_json": RGBA_B64, "z_index": 1}],
            [{"url": REMOTE_URL, "z_index": 0}],
        ):
            with self.subTest(count=len(items)):
                request = self.request(prompt=f"分层异常夹具 {len(items)}", background="transparent")
                action = self.propose(request=request.model_dump(exclude_none=True))
                job = self.job(action)
                stack, calls = self.clients(items)
                with stack:
                    self.assertEqual(self.runtime().handle(self.message(job["id"]))["status"], "failed")
                self.assertEqual([call[0] for call in calls], ["POST"])
                self.assertEqual(list_assets(job_id=job["id"]), [])
                with closing(db_connect()) as conn:
                    count = conn.execute("SELECT count(*) FROM generations WHERE job_id=?", (job["id"],)).fetchone()[0]
                    self.assertEqual(count, 0)

    def test_single_image_response_does_not_strip_layer_metadata_or_reported_errors(self):
        for fields in (
            {"z_index": 1},
            {"bounding_box": {"absolute": [0, 0, 1, 1]}},
            {"text_recognition": {"text": "fixture layer"}},
            {"error": {"code": "fixture_error", "message": "private upstream detail"}},
        ):
            stack, calls = self.clients([{"url": REMOTE_URL, **fields}])
            with self.subTest(fields=fields), stack, self.assertRaises(ProviderProtocolError) as error:
                asyncio.run(ByteDanceImageProvider().generate(self.request(), TARGET))
            self.assertNotIn("private upstream detail", str(error.exception))
            self.assertEqual([call[0] for call in calls], ["POST"])

    def test_mismatched_declared_or_encoded_format_is_rejected(self):
        for item in (
            {"url": REMOTE_URL, "output_format": "jpeg"},
            {"b64_json": base64.b64encode(b"\xff\xd8\xffwrong").decode()},
            {"b64_json": "malformed-base64"},
        ):
            stack, calls = self.clients([item])
            with self.subTest(item=item), stack, self.assertRaises(ProviderProtocolError):
                asyncio.run(ByteDanceImageProvider().generate(self.request(background="transparent"), TARGET))
            self.assertEqual(len(calls), 1)

    def test_frozen_transparent_background_cannot_be_changed_after_confirmation(self):
        job = self.job(self.propose(request=self.request(background="transparent").model_dump(exclude_none=True)))
        with closing(db_connect()) as conn:
            payload = json.loads(job["input_json"])
            payload["request"]["background"] = "opaque"
            conn.execute("UPDATE jobs SET input_json=? WHERE id=?", (json.dumps(payload), job["id"]))
            conn.commit()
        stack, calls = self.clients([{"url": REMOTE_URL}])
        with stack:
            self.assert_rejected(self.runtime(), get_job(job["id"]))
        self.assertEqual(calls, [])

    def test_web_uses_declared_background_and_format_for_both_operations(self):
        model = next(item for item in catalog_api_response(load_provider_catalog())["models"] if item["id"] == "seedream-5-5")
        self.assertTrue(run_studio_module_script(r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
globalThis.localStorage={getItem:()=>null,setItem:()=>{}};
globalThis.document={documentElement:{lang:''}};
globalThis.window={dispatchEvent:()=>{}};
const {model}=JSON.parse(fs.readFileSync(0,'utf8'));
const {buildOperationPayload}=await import('./studio/features/generate-image/operation-payload.js');
const {setLanguage,supportedLanguages,t}=await import('./studio/i18n.js');
for(const language of supportedLanguages){
  setLanguage(language);
  assert.notEqual(t('generateImage.value.background.transparent'),'generateImage.value.background.transparent');
  for(const operation of ['generate','edit']){
    assert.deepEqual(buildOperationPayload(model,{operation,background:'transparent',output_format:'png',layer_decomposition:true}),
      {operation,background:'transparent',output_format:'png'});
  }
}
console.log(JSON.stringify({ok:true}));
""", {"model": model})["ok"])
