"""验证模型标识的唯一解析、显式渠道和真实请求拒绝边界。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from studio_test_client import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.model_routes import InvalidModelSelector, catalog_image_aliases, resolve_image_target
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.routing import MODEL_ALIASES, resolve_chain
from angemedia_gateway.server import app
from angemedia_gateway.state import ensure_default_admin_user, init_db


class ModelRouteBoundaryTest(unittest.TestCase):
    def test_every_image_catalog_id_alias_and_native_name_resolves(self) -> None:
        with patch("angemedia_gateway.routing.builtin_provider_enabled", return_value=True):
            for model in load_provider_catalog().models:
                if model.media_type != "image":
                    continue
                for selector in (model.id, model.provider_model, *model.aliases):
                    with self.subTest(selector=selector):
                        for value in (selector, f"{model.provider}::{selector}"):
                            chain = resolve_chain(value)
                            self.assertEqual([(item.provider, item.model) for item in chain], [(model.provider, model.provider_model)])

    def test_unknown_unscoped_cross_provider_and_video_models_are_rejected(self) -> None:
        for value in ("agnes-2-55", "UnknownOrg/NewModel", "modelscope::agnes-2-5", "agnes-video-2-5", "agnes_video::agnes-video-2.5", "unknown::x", "modelscope::", "modelscope::x::y", "   ", "modelscope::x\nsecret"):
            with self.subTest(selector=value), self.assertRaises(InvalidModelSelector):
                resolve_image_target(value, MODEL_ALIASES)

    def test_explicit_unknown_native_id_keeps_provider_and_model_exact(self) -> None:
        target = resolve_image_target("siliconflow::ExampleOrg/NewModel", MODEL_ALIASES)
        self.assertEqual((target.provider, target.model), ("siliconflow", "ExampleOrg/NewModel"))
        with patch("angemedia_gateway.routing.builtin_provider_enabled", return_value=False):
            self.assertEqual(resolve_chain("siliconflow::ExampleOrg/NewModel"), [])

    def test_ambiguous_native_id_needs_provider_scope(self) -> None:
        catalog = load_provider_catalog()
        images = [model for model in catalog.models if model.id in {"kolors", "qwen"}]
        collision = replace(catalog, models=tuple(replace(model, provider_model="Shared/NewModel") for model in images))
        with patch("angemedia_gateway.model_routes._catalog", return_value=collision):
            aliases = catalog_image_aliases()
            with self.assertRaises(InvalidModelSelector):
                resolve_image_target("Shared/NewModel", aliases)
            self.assertEqual(resolve_image_target("siliconflow::Shared/NewModel", aliases).provider, "siliconflow")
            self.assertEqual(resolve_image_target("modelscope::Shared/NewModel", aliases).provider, "modelscope")


class ModelRouteApiTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="model-route-api-")
        self.addCleanup(tmp.cleanup)
        database = patch.object(C, "DB_FILE", Path(tmp.name) / "test.db")
        database.start()
        self.addCleanup(database.stop)
        env = patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_DEFAULT_PASSWORD": "test-password-only"})
        env.start()
        self.addCleanup(env.stop)
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.assertEqual(self.client.post("/v1/admin/login", json={"username": "admin", "password": "test-password-only"}).status_code, 200)

    def test_invalid_model_does_not_admit_jobs_or_touch_network(self) -> None:
        with patch("httpx.AsyncClient") as network:
            for path in ("/v1/images/generations", "/v1/admin/jobs/images"):
                response = self.client.post(path, json={"prompt": "测试边界", "model": "agnes-2-55"})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"]["code"], "invalid_image_request")
            response = self.client.post("/v1/media/route", json={"prompt": "测试边界", "media_type": "image", "requested_model": "agnes-2-55"})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()["detail"]["code"], "invalid_model_selector")
            network.assert_not_called()
        with closing(db_connect()) as conn:
            for table in ("jobs", "job_dispatches", "job_attempts"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_catalog_internal_id_is_admitted_to_correct_provider(self) -> None:
        self.client.post("/v1/admin/provider-configs/agnes_image", json={"enabled": True})
        response = self.client.post("/v1/admin/jobs/images", json={"prompt": "测试目录内部ID", "model": "agnes-2-5"})
        self.assertEqual(response.status_code, 202, response.text)
        with closing(db_connect()) as conn:
            job = conn.execute("SELECT provider,model FROM jobs WHERE id=?", (response.json()["job_id"],)).fetchone()
        self.assertEqual(tuple(job), ("agnes_image", "agnes-image-2.5-flash"))

    def test_local_budget_error_keeps_its_meaning_through_the_generation_api(self) -> None:
        self.client.post("/v1/admin/provider-configs/modelscope", json={"enabled": True, "api_key": "TEST_ONLY_NOT_A_REAL_KEY"})
        with patch.object(C, "MODELSCOPE_DAILY_LIMIT", 0), patch.object(C, "QUOTA_FILE", C.DB_FILE.parent / "absent-budget.json"), patch("httpx.AsyncClient") as network:
            response = self.client.post("/v1/images/generations", json={"prompt": "测试本地预算提示", "model": "qwen"})
        self.assertEqual(response.status_code, 502, response.text)
        details = response.json()["detail"]
        self.assertEqual(details["error_category"], "local_budget")
        self.assertFalse(details["retryable"])
        self.assertIn("本地", details["human_hint"])
        network.return_value.__aenter__.return_value.post.assert_not_called()
