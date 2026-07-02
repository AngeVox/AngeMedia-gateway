"""Prompt enhance API contract tests."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

from fastapi.testclient import TestClient  # noqa: E402

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.state import create_gateway_api_key, ensure_default_admin_user, init_db  # noqa: E402


class PromptEnhanceApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="prompt-enhance-api-")
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
        item = create_gateway_api_key(name="prompt-enhance")
        return {"Authorization": f"Bearer {item['key']}"}

    def assert_safe_response(self, response_text: str) -> None:
        forbidden = [
            "sk-LEAKED-SECRET-MUST-NOT-APPEAR",
            "Bearer leak-token",
            "Authorization",
            "provider_raw_body",
            "request_hash",
            "C:\\Users\\admin\\secret.png",
            "data:image/png;base64",
            "password_hash",
        ]
        for marker in forbidden:
            self.assertNotIn(marker, response_text)

    def test_unauthenticated_prompt_enhance_rejected(self) -> None:
        response = self.client.post("/v1/prompt/enhance", json={"prompt": "帮我画只猫"})
        self.assertEqual(response.status_code, 401)

    def test_gateway_api_key_can_access_prompt_enhance(self) -> None:
        response = self.client.post(
            "/v1/prompt/enhance",
            json={"prompt": "帮我画只猫", "media_type": "image", "language": "zh"},
            headers=self.gateway_headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["mode"], "expand")
        self.assertTrue(body["changed"])
        self.assertIn("cat", body["model_prompt_en"].lower())
        self.assertIn("composition", body["model_prompt_en"].lower())
        self.assertIn("light", body["model_prompt_en"].lower())
        self.assertIn("user_display_prompt_zh", body)
        self.assertIn("model_prompt_en", body)

    def test_admin_session_can_access_prompt_enhance(self) -> None:
        self.login_admin()
        response = self.client.post("/v1/prompt/enhance", json={"prompt": "帮我画只猫"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_detailed_chinese_image_prompt_only_polishes(self) -> None:
        self.login_admin()
        prompt = "一只橘猫坐在窗台上，旁边有一杯热茶，下午阳光照进房间，画面温暖安静"
        response = self.client.post("/v1/prompt/enhance", json={"prompt": prompt, "media_type": "image"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["mode"], "polish")
        model_prompt = body["model_prompt_en"].lower()
        self.assertIn("orange cat", model_prompt)
        self.assertIn("windowsill", model_prompt)
        self.assertIn("warm afternoon", model_prompt)
        self.assertNotIn("cyberpunk", model_prompt)
        self.assertNotIn("future city", model_prompt)

    def test_chinese_video_prompt_uses_motion_and_camera_language(self) -> None:
        self.login_admin()
        response = self.client.post(
            "/v1/prompt/enhance",
            json={"prompt": "一只猫从沙发跳到窗台", "media_type": "video"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn(body["mode"], {"expand", "polish"})
        model_prompt = body["model_prompt_en"].lower()
        self.assertIn("cat", model_prompt)
        self.assertIn("sofa", model_prompt)
        self.assertIn("windowsill", model_prompt)
        self.assertIn("motion", model_prompt)
        self.assertIn("camera", model_prompt)

    def test_english_prompt_is_not_forced_to_chinese_for_display_language_en(self) -> None:
        self.login_admin()
        response = self.client.post(
            "/v1/prompt/enhance",
            json={
                "prompt": "a cute cat sitting by the window, warm afternoon light",
                "media_type": "image",
                "language": "en",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["mode"], "polish")
        self.assertEqual(body["input_summary"]["display_language"], "en")
        self.assertIn("cute cat", body["model_prompt_en"].lower())
        self.assertIn("warm afternoon light", body["model_prompt_en"].lower())
        self.assertIn("cute cat", body["user_display_prompt"].lower())
        self.assertNotIn("一只", body["user_display_prompt"])

    def test_response_sanitizes_sensitive_legacy_or_polluted_prompt(self) -> None:
        self.login_admin()
        polluted = (
            "帮我画只猫 Authorization: Bearer leak-token-1234567890 "
            "sk-LEAKED-SECRET-MUST-NOT-APPEAR request_hash=abc123 "
            "provider_raw_body C:\\Users\\admin\\secret.png data:image/png;base64,AAAA"
        )
        response = self.client.post("/v1/prompt/enhance", json={"prompt": polluted, "media_type": "image"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_safe_response(response.text)

    def test_blank_and_too_long_prompt_rejected(self) -> None:
        self.login_admin()
        blank = self.client.post("/v1/prompt/enhance", json={"prompt": "   "})
        self.assertIn(blank.status_code, {400, 422})
        too_long = self.client.post("/v1/prompt/enhance", json={"prompt": "a" * 4001})
        self.assertEqual(too_long.status_code, 422)

    def test_prompt_enhance_does_not_call_external_assistant_llm(self) -> None:
        self.login_admin()
        with patch("angemedia_gateway.outbound_http.httpx.AsyncClient") as client_cls:
            response = self.client.post("/v1/prompt/enhance", json={"prompt": "帮我画只猫"})
        self.assertEqual(response.status_code, 200, response.text)
        client_cls.assert_not_called()

    def test_assistant_plan_restored_and_generate_route_remains_404(self) -> None:
        self.login_admin()
        plan = self.client.post("/v1/assistant/plan", json={"message": "帮我画只猫"})
        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertTrue(plan.json()["requires_user_confirmation"])

        generate = self.client.post("/v1/assistant/generate", json={"message": "test"})
        self.assertEqual(generate.status_code, 404, generate.text)


if __name__ == "__main__":
    unittest.main()
