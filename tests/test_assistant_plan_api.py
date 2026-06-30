"""Assistant plan API contract tests."""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

from fastapi.testclient import TestClient  # noqa: E402

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.state import create_gateway_api_key, ensure_default_admin_user, init_db  # noqa: E402


class AssistantPlanApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="assistant-plan-api-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._orig_db = C.DB_FILE
        self._orig_gateway_key = C.GATEWAY_API_KEY
        C.DB_FILE = self._db_path
        C.GATEWAY_API_KEY = ""
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        C.DB_FILE = self._orig_db
        C.GATEWAY_API_KEY = self._orig_gateway_key
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def login_admin(self) -> None:
        response = self.client.post("/v1/admin/login", json={"username": "admin", "password": "admin123456"})
        self.assertEqual(response.status_code, 200, response.text)

    def gateway_headers(self) -> dict[str, str]:
        item = create_gateway_api_key(name="assistant-plan")
        return {"Authorization": f"Bearer {item['key']}"}

    def table_count(self, table: str) -> int:
        with sqlite3.connect(C.DB_FILE) as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def assert_safe_response(self, text: str) -> None:
        forbidden = [
            "sk-LEAKED-SECRET-MUST-NOT-APPEAR",
            "Bearer leak-token",
            "Authorization",
            "provider_raw_body",
            "request_hash",
            "C:\\Users\\admin\\secret.png",
            "data:image/png;base64",
            "password_hash",
            "raw LLM",
        ]
        for marker in forbidden:
            self.assertNotIn(marker, text)

    def test_unauthenticated_assistant_plan_rejected(self) -> None:
        response = self.client.post("/v1/assistant/plan", json={"message": "帮我画一只猫"})
        self.assertEqual(response.status_code, 401)

    def test_gateway_api_key_can_call_assistant_plan(self) -> None:
        response = self.client.post(
            "/v1/assistant/plan",
            json={"message": "帮我生成一张猫在窗边晒太阳的图", "media_type": "auto", "language": "zh"},
            headers=self.gateway_headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["media_type"], "image")
        self.assertEqual(body["route"]["target_page"], "generate-image")
        self.assertTrue(body["requires_user_confirmation"])
        self.assertEqual(body["mode"], "local_recommendation")
        self.assertEqual(body["assistant_status"]["planner"], "local_recommendation")
        self.assertFalse(body["assistant_status"]["llm_used"])
        self.assertIn("model_prompt_en", body["prompt"])
        self.assertIn("user_display_prompt_zh", body["prompt"])
        self.assertIn("cat", body["prompt"]["model_prompt_en"].lower())

    def test_llm_enabled_assistant_plan_uses_formal_llm_planner_shape(self) -> None:
        self.login_admin()
        os.environ["ANGE_ASSISTANT_ENABLED"] = "true"
        os.environ["ANGE_LLM_API_KEY"] = "sk-test-assistant-not-returned"
        llm_plan = {
            "media_type": "image",
            "model": "qwen",
            "prompt": "A cinematic orange cat sitting by a rainy window, detailed fur, soft warm light.",
            "size": "1024x1024",
            "negative_prompt": "watermark, low quality",
            "assistant_message": "我已整理成可执行的图片生成计划。",
            "prompt_changes": ["补充环境", "补充光影"],
            "work_steps": ["判断媒体类型", "选择模型", "等待用户确认"],
        }
        try:
            with patch("angemedia_gateway.assistant.call_llm_for_plan", new=AsyncMock(return_value=llm_plan)):
                response = self.client.post(
                    "/v1/assistant/plan",
                    json={"message": "帮我画一只雨天窗边的橘猫", "media_type": "image", "language": "zh"},
                )
        finally:
            os.environ.pop("ANGE_ASSISTANT_ENABLED", None)
            os.environ.pop("ANGE_LLM_API_KEY", None)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["media_type"], "image")
        self.assertEqual(body["mode"], "llm_recommendation")
        self.assertTrue(body["assistant_status"]["llm_used"])
        self.assertEqual(body["route"]["target_page"], "generate-image")
        self.assertEqual(body["prompt"]["model_prompt_en"], llm_plan["prompt"])
        self.assertEqual(body["prompt"]["negative_prompt"], "watermark, low quality")
        self.assertTrue(body["requires_user_confirmation"])
        self.assert_safe_response(response.text)

    def test_llm_chinese_prompt_is_normalized_to_english_model_prompt(self) -> None:
        self.login_admin()
        os.environ["ANGE_ASSISTANT_ENABLED"] = "true"
        os.environ["ANGE_LLM_API_KEY"] = "sk-test-assistant-not-returned"
        llm_plan = {
            "media_type": "image",
            "model": "qwen",
            "prompt": "一只毛茸茸的奶黄色小猫咪，蜷缩在米白色羊毛毯上，午后阳光，浅景深。",
            "size": "1024x1024",
            "assistant_message": "我已整理成可执行的图片生成计划。",
        }
        try:
            with patch("angemedia_gateway.assistant.call_llm_for_plan", new=AsyncMock(return_value=llm_plan)):
                response = self.client.post(
                    "/v1/assistant/plan",
                    json={"message": "生成一只小猫咪", "media_type": "image", "language": "zh"},
                )
        finally:
            os.environ.pop("ANGE_ASSISTANT_ENABLED", None)
            os.environ.pop("ANGE_LLM_API_KEY", None)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        model_prompt = body["prompt"]["model_prompt_en"]
        self.assertNotRegex(model_prompt, r"[\u4e00-\u9fff]")
        self.assertIn("kitten", model_prompt.lower())
        self.assertIn("sunlight", model_prompt.lower())
        self.assertIn("一只毛茸茸", body["prompt"]["user_display_prompt_zh"])

    def test_llm_failure_returns_explicit_config_error_fallback(self) -> None:
        from angemedia_gateway.providers.errors import BackendUnavailable

        self.login_admin()
        os.environ["ANGE_ASSISTANT_ENABLED"] = "true"
        os.environ["ANGE_LLM_API_KEY"] = "sk-test-assistant-not-returned"
        try:
            with patch(
                "angemedia_gateway.assistant.call_llm_for_plan",
                new=AsyncMock(side_effect=BackendUnavailable("upstream failed sk-secret")),
            ):
                response = self.client.post(
                    "/v1/assistant/plan",
                    json={"message": "帮我画一只猫", "media_type": "image", "language": "zh"},
                )
        finally:
            os.environ.pop("ANGE_ASSISTANT_ENABLED", None)
            os.environ.pop("ANGE_LLM_API_KEY", None)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["mode"], "local_recommendation")
        self.assertEqual(body["assistant_status"]["mode"], "config_error")
        self.assertFalse(body["assistant_status"]["llm_used"])
        self.assertNotIn("sk-secret", response.text)

    def test_admin_session_can_call_assistant_plan(self) -> None:
        self.login_admin()
        response = self.client.post("/v1/assistant/plan", json={"message": "帮我画一只猫", "language": "zh"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_chinese_video_request_routes_to_generate_video(self) -> None:
        self.login_admin()
        response = self.client.post(
            "/v1/assistant/plan",
            json={"message": "帮我生成一段猫从沙发跳到窗台的视频", "media_type": "auto", "language": "zh"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["media_type"], "video")
        self.assertEqual(body["route"]["target_page"], "generate-video")
        self.assertIn("motion", body["prompt"]["model_prompt_en"].lower())
        self.assertIn("确认", " ".join(body["work_steps"]))

    def test_english_ui_is_not_forced_to_chinese_display(self) -> None:
        self.login_admin()
        response = self.client.post(
            "/v1/assistant/plan",
            json={
                "message": "create a warm product photo of a ceramic mug by the window",
                "media_type": "image",
                "language": "en",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("recommend", body["assistant_message"].lower())
        self.assertIn("ceramic mug", body["prompt"]["model_prompt_en"].lower())
        self.assertNotIn("我建议", body["assistant_message"])

    def test_chinese_visual_keywords_are_preserved_in_english_model_prompt(self) -> None:
        self.login_admin()
        response = self.client.post(
            "/v1/assistant/plan",
            json={"message": "帮我生成一张赛博朋克城市夜景，雨天，霓虹招牌，电影感", "media_type": "image", "language": "zh"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        model_prompt = response.json()["prompt"]["model_prompt_en"].lower()
        for phrase in ("cyberpunk", "city", "night", "rain", "neon", "cinematic"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, model_prompt)

    def test_assistant_plan_has_no_generation_side_effects_or_external_llm(self) -> None:
        self.login_admin()
        jobs_before = self.table_count("jobs")
        generations_before = self.table_count("generations")
        with (
            patch("angemedia_gateway.assistant.httpx.AsyncClient") as assistant_httpx,
            patch("angemedia_gateway.routes.media.media_service.create_image") as create_image,
            patch("angemedia_gateway.routes.media.media_service.create_video") as create_video,
        ):
            response = self.client.post("/v1/assistant/plan", json={"message": "帮我画只猫"})
        self.assertEqual(response.status_code, 200, response.text)
        assistant_httpx.assert_not_called()
        create_image.assert_not_called()
        create_video.assert_not_called()
        self.assertEqual(self.table_count("jobs"), jobs_before)
        self.assertEqual(self.table_count("generations"), generations_before)
        self.assertEqual(self.table_count("assistant_plans"), 1)

    def test_blank_and_too_long_message_rejected(self) -> None:
        self.login_admin()
        blank = self.client.post("/v1/assistant/plan", json={"message": "   "})
        self.assertIn(blank.status_code, {400, 422})
        too_long = self.client.post("/v1/assistant/plan", json={"message": "a" * 4001})
        self.assertEqual(too_long.status_code, 422)

    def test_secret_like_input_is_sanitized_in_response(self) -> None:
        self.login_admin()
        polluted = (
            "帮我画只猫 Authorization: Bearer leak-token-1234567890 "
            "sk-LEAKED-SECRET-MUST-NOT-APPEAR request_hash=abc123 "
            "provider_raw_body C:\\Users\\admin\\secret.png data:image/png;base64,AAAA"
        )
        response = self.client.post("/v1/assistant/plan", json={"message": polluted, "media_type": "image"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_safe_response(response.text)

    def test_assistant_generate_remains_404(self) -> None:
        self.login_admin()
        response = self.client.post("/v1/assistant/generate", json={"message": "test"})
        self.assertEqual(response.status_code, 404, response.text)

    def test_existing_prompt_enhance_remains_working(self) -> None:
        self.login_admin()
        response = self.client.post("/v1/prompt/enhance", json={"prompt": "帮我画只猫", "media_type": "image"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("model_prompt_en", response.json())


if __name__ == "__main__":
    unittest.main()
