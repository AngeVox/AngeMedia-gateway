"""Assistant-backed Prompt Copilot API contracts."""
from __future__ import annotations

import os
import shutil
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
from angemedia_gateway.services.assistant_skills import load_assistant_skill  # noqa: E402


class AssistantPromptCopilotApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="assistant-prompt-copilot-")
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
        item = create_gateway_api_key(name="prompt-copilot")
        return {"Authorization": f"Bearer {item['key']}"}

    def assert_safe(self, text: str) -> None:
        for marker in (
            "sk-LEAKED-SECRET-MUST-NOT-APPEAR",
            "Bearer leak-token",
            "Authorization",
            "provider_raw_body",
            "request_hash",
            "C:\\Users\\admin\\secret.png",
            "data:image/png;base64",
        ):
            self.assertNotIn(marker, text)

    def test_bundled_skill_loader_is_read_only_metadata(self) -> None:
        skill = load_assistant_skill("video_prompt_planner")
        self.assertEqual(skill.id, "video_prompt_planner")
        self.assertIn("local_prompt_enhancer", skill.allowed_tools)
        self.assertIn("model prompt in English", skill.body)
        with self.assertRaises(ValueError):
            load_assistant_skill("../escape")

    def test_prompt_copilot_requires_admin_session(self) -> None:
        response = self.client.post("/v1/assistant/prompt-copilot", json={"prompt": "帮我画只猫"})
        self.assertEqual(response.status_code, 401)

    def test_gateway_key_cannot_use_prompt_copilot_llm(self) -> None:
        response = self.client.post(
            "/v1/assistant/prompt-copilot",
            json={"prompt": "帮我画只猫"},
            headers=self.gateway_headers(),
        )
        self.assertEqual(response.status_code, 403)

    def test_prompt_copilot_falls_back_to_local_when_llm_unconfigured(self) -> None:
        self.login_admin()
        response = self.client.post(
            "/v1/assistant/prompt-copilot",
            json={"prompt": "帮我画只猫", "media_type": "image", "language": "zh"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["assistant_status"]["mode"], "local_fallback")
        self.assertFalse(body["assistant_status"]["llm_used"])
        self.assertEqual(body["skill"]["id"], "image_prompt_planner")
        self.assertGreaterEqual(len(body["timeline"]), 2)
        self.assertIn("cat", body["model_prompt_en"].lower())
        self.assertIn("route", body)
        self.assertIn("suggested_params", body)
        self.assertEqual(body["route"]["target_page"], "generate-image")

    def test_prompt_copilot_uses_llm_and_keeps_model_prompt_english(self) -> None:
        self.login_admin()
        os.environ["ANGE_ASSISTANT_ENABLED"] = "true"
        os.environ["ANGE_LLM_API_KEY"] = "sk-test-not-returned"
        os.environ["ANGE_LLM_BASE_URL"] = "http://llm.test/v1"
        os.environ["ANGE_LLM_MODEL"] = "qwen"
        llm_payload = {
            "mode": "expand",
            "user_display_prompt_zh": "一只猫在窗边晒太阳，画面温暖。",
            "model_prompt_en": "a cat sitting by the window in warm sunlight, clean composition",
            "model_hint": "flux",
            "recommended_size": "1024x1024",
            "notes_zh": ["补充了光线和构图。"],
            "warnings": [],
        }
        try:
            with patch("angemedia_gateway.services.prompt_copilot.call_llm_for_prompt_copilot", new=AsyncMock(return_value=llm_payload)):
                response = self.client.post(
                    "/v1/assistant/prompt-copilot",
                    json={"prompt": "帮我生成一只猫晒太阳", "media_type": "image", "language": "zh"},
                )
        finally:
            for key in ("ANGE_ASSISTANT_ENABLED", "ANGE_LLM_API_KEY", "ANGE_LLM_BASE_URL", "ANGE_LLM_MODEL"):
                os.environ.pop(key, None)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["assistant_status"]["llm_used"])
        self.assertEqual(body["assistant_status"]["mode"], "llm")
        self.assertEqual(body["model_prompt_en"], llm_payload["model_prompt_en"])
        self.assertNotRegex(body["model_prompt_en"], r"[\u4e00-\u9fff]")
        self.assertEqual(body["route"]["provider"], "modelscope")
        self.assertEqual(body["route"]["model"], "flux")
        self.assertEqual(body["suggested_params"]["size"], "1024x1024")
        self.assertIn("timeline", body)
        self.assert_safe(response.text)

    def test_prompt_copilot_video_path_uses_video_skill(self) -> None:
        self.login_admin()
        response = self.client.post(
            "/v1/assistant/prompt-copilot",
            json={"prompt": "帮我生成一段猫从沙发跳到窗台的视频", "media_type": "video", "language": "zh"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["input_summary"]["media_type"], "video")
        self.assertEqual(body["skill"]["id"], "video_prompt_planner")
        self.assertEqual(body["route"]["provider"], "agnes_video")
        self.assertEqual(body["route"]["model"], "agnes-video-v2.0")
        self.assertEqual(body["suggested_params"]["size"], "1152x768")
        self.assertIn("camera", body["model_prompt_en"].lower())
        self.assertIn("motion", body["model_prompt_en"].lower())

    def test_english_user_is_not_forced_to_chinese_display(self) -> None:
        self.login_admin()
        response = self.client.post(
            "/v1/assistant/prompt-copilot",
            json={"prompt": "a cozy product photo of a ceramic mug by the window", "media_type": "image", "language": "en"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["input_summary"]["display_language"], "en")
        self.assertIn("ceramic mug", body["user_display_prompt"].lower())
        self.assertNotRegex(body["user_display_prompt"], r"[\u4e00-\u9fff]")

    def test_prompt_copilot_sanitizes_sensitive_prompt(self) -> None:
        self.login_admin()
        polluted = (
            "帮我画只猫 Authorization: Bearer leak-token "
            "sk-LEAKED-SECRET-MUST-NOT-APPEAR request_hash=abc123 "
            "provider_raw_body C:\\Users\\admin\\secret.png data:image/png;base64,AAAA"
        )
        response = self.client.post("/v1/assistant/prompt-copilot", json={"prompt": polluted, "media_type": "image"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_safe(response.text)


if __name__ == "__main__":
    unittest.main()
