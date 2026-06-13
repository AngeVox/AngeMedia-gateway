from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("IMAGE_PROXY_STATE_DIR", tempfile.mkdtemp(prefix="angemedia-test-"))
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("AUTO_DOWNLOAD_GENERATED", "false")
os.environ.setdefault("SILICONFLOW_API_KEY", "sf-test-secret-value")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")

from fastapi.testclient import TestClient  # noqa: E402

from angemedia_gateway.server import app  # noqa: E402
import angemedia_gateway.config as C  # noqa: E402


class GatewaySmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def login_admin(self) -> None:
        response = self.client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_public_routes_are_available(self) -> None:
        for path in ["/", "/studio", "/api-docs", "/health"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)

    def assert_web_studio_index(self, response) -> None:
        """Web Studio 正式入口应加载 v0.2.0 studio shell，而不是旧 admin。"""
        self.assertEqual(response.status_code, 200, response.text)
        body = response.text
        self.assertIn("AngeMedia Studio", body)
        self.assertIn('/assets/studio/app.js', body)
        self.assertNotIn('/assets/admin.js', body)
        self.assertNotIn("AngeMedia Admin", body)

    def test_root_and_studio_routes_serve_web_studio(self) -> None:
        """/ 和 /studio 继续作为 v0.2.0 Web Studio 入口。"""
        for path in ["/", "/studio"]:
            with self.subTest(path=path):
                self.assert_web_studio_index(self.client.get(path))

    def assert_redirects_to_web_studio(self, response) -> None:
        """legacy /admin 应转向 Web Studio，而不是继续服务旧 admin.html。"""
        self.assertIn(response.status_code, (302, 303, 307, 308), response.text)
        location = response.headers.get("location", "")
        self.assertIn(
            location,
            {
                "/#/dashboard",
                "/studio#/dashboard",
                "http://testserver/#/dashboard",
                "http://testserver/studio#/dashboard",
            },
        )

    def test_admin_route_redirects_to_web_studio(self) -> None:
        """/admin 不应再作为旧 v0.1.0 admin frontend 入口。"""
        response = self.client.get("/admin", follow_redirects=False)
        self.assert_redirects_to_web_studio(response)

    def test_admin_slash_route_does_not_serve_legacy_admin(self) -> None:
        """/admin/ 也不能成为旧 admin.html 的残留入口。"""
        response = self.client.get("/admin/", follow_redirects=False)
        if response.status_code in {302, 303, 307, 308}:
            location = response.headers.get("location", "")
            if location in {"/admin", "http://testserver/admin"}:
                response = self.client.get(location, follow_redirects=False)
            self.assert_redirects_to_web_studio(response)
            return

        body = response.text
        self.assertNotIn("AngeMedia Admin", body)
        self.assertNotIn('/assets/admin.js', body)

    def test_auth_required_routes_require_auth(self) -> None:
        """/v1/models 等 require_auth 路由在无 key 时返回 401。"""
        orig = C.GATEWAY_API_KEY
        try:
            C.GATEWAY_API_KEY = ""
            response = self.client.get("/v1/models")
            self.assertEqual(response.status_code, 401)
        finally:
            C.GATEWAY_API_KEY = orig

    def test_admin_session_can_access_require_auth_route(self) -> None:
        """admin session 可以访问 require_auth route。"""
        self.login_admin()
        response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 200)

    def test_admin_config_metadata_and_secret_masking(self) -> None:
        self.login_admin()

        metadata = self.client.get("/v1/admin/config-metadata")
        self.assertEqual(metadata.status_code, 200, metadata.text)
        self.assertGreaterEqual(len(metadata.json().get("groups", [])), 5)

        config = self.client.get("/v1/admin/config")
        self.assertEqual(config.status_code, 200, config.text)
        settings = config.json()["settings"]
        self.assertIn("SILICONFLOW_API_KEY", settings)
        self.assertNotEqual(settings["SILICONFLOW_API_KEY"], "sf-test-secret-value")
        self.assertIn("*", settings["SILICONFLOW_API_KEY"])

    def test_admin_config_rejects_invalid_values(self) -> None:
        self.login_admin()
        invalid_payloads = [
            {"AUTO_DOWNLOAD_GENERATED": "not-a-bool"},
            {"MEDIA_DOWNLOAD_MAX_BYTES": "not-an-int"},
            {"PUBLIC_BASE_URL": "ftp://example.test"},
        ]

        for settings in invalid_payloads:
            with self.subTest(settings=settings):
                response = self.client.post("/v1/admin/config", json={"settings": settings})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn("detail", response.json())

    def test_health_returns_ok(self) -> None:
        """/health 精确等于 {"status": "ok"}。"""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_no_forbidden_fields(self) -> None:
        """/health 不包含敏感字段。"""
        response = self.client.get("/health")
        body = response.json()
        forbidden = [
            "name", "version", "auth_enabled",
            "siliconflow", "modelscope", "pollinations",
            "openai_image", "agnes_image", "agnes_video",
            "storage_ready", "assistant", "models",
            "configured", "enabled",
        ]
        for field in forbidden:
            self.assertNotIn(field, body, f"/health 不应包含 {field}")

    def test_generated_file_requires_auth(self) -> None:
        """/generated/{filename} 无鉴权返回 401。"""
        orig = C.GATEWAY_API_KEY
        try:
            C.GATEWAY_API_KEY = "test-key-for-auth"
            response = self.client.get("/generated/nonexistent.png")
            self.assertIn(response.status_code, (401, 403))
        finally:
            C.GATEWAY_API_KEY = orig

    def test_uploads_file_requires_auth(self) -> None:
        """/uploads/{filename} 无鉴权返回 401。"""
        orig = C.GATEWAY_API_KEY
        try:
            C.GATEWAY_API_KEY = "test-key-for-auth"
            response = self.client.get("/uploads/nonexistent.mp4")
            self.assertIn(response.status_code, (401, 403))
        finally:
            C.GATEWAY_API_KEY = orig

    def test_generated_real_file_with_auth_returns_200(self) -> None:
        """带 auth 访问真实 generated 文件返回 200 且内容正确。"""
        test_file = C.OUTPUT_DIR / "smoke-test.png"
        test_content = b"smoke test png content"
        test_file.write_bytes(test_content)
        try:
            self.login_admin()
            response = self.client.get("/generated/smoke-test.png")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, test_content)
        finally:
            test_file.unlink(missing_ok=True)

    def test_uploads_real_file_with_auth_returns_200(self) -> None:
        """带 auth 访问真实 upload 文件返回 200 且内容正确。"""
        test_file = C.UPLOAD_DIR / "smoke-test.mp4"
        test_content = b"smoke test mp4 content"
        test_file.write_bytes(test_content)
        try:
            self.login_admin()
            response = self.client.get("/uploads/smoke-test.mp4")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, test_content)
        finally:
            test_file.unlink(missing_ok=True)

    def test_head_generated_and_uploads_with_auth_returns_200(self) -> None:
        """带 auth 的 HEAD /generated 和 /uploads 返回 200，不是 405。"""
        generated_file = C.OUTPUT_DIR / "head-smoke.png"
        upload_file = C.UPLOAD_DIR / "head-smoke.mp4"
        generated_file.write_bytes(b"head generated")
        upload_file.write_bytes(b"head upload")
        try:
            self.login_admin()
            generated = self.client.head("/generated/head-smoke.png")
            self.assertEqual(generated.status_code, 200, generated.text)
            self.assertIn("image/png", generated.headers.get("content-type", ""))
            uploaded = self.client.head("/uploads/head-smoke.mp4")
            self.assertEqual(uploaded.status_code, 200, uploaded.text)
            self.assertIn("video/mp4", uploaded.headers.get("content-type", ""))
        finally:
            generated_file.unlink(missing_ok=True)
            upload_file.unlink(missing_ok=True)

    def test_head_generated_and_uploads_without_auth_returns_401(self) -> None:
        """无 auth 的 HEAD /generated 和 /uploads 仍受鉴权保护。"""
        generated_file = C.OUTPUT_DIR / "head-no-auth.png"
        upload_file = C.UPLOAD_DIR / "head-no-auth.mp4"
        generated_file.write_bytes(b"head no auth generated")
        upload_file.write_bytes(b"head no auth upload")
        try:
            orig = C.GATEWAY_API_KEY
            try:
                C.GATEWAY_API_KEY = "test-key-for-auth"
                generated = self.client.head("/generated/head-no-auth.png")
                self.assertIn(generated.status_code, (401, 403))
                uploaded = self.client.head("/uploads/head-no-auth.mp4")
                self.assertIn(uploaded.status_code, (401, 403))
            finally:
                C.GATEWAY_API_KEY = orig
        finally:
            generated_file.unlink(missing_ok=True)
            upload_file.unlink(missing_ok=True)

    def test_generated_nonexistent_returns_404(self) -> None:
        """访问不存在的 generated 文件返回 404。"""
        self.login_admin()
        response = self.client.get("/generated/does-not-exist.png")
        self.assertEqual(response.status_code, 404)

    def test_uploads_nonexistent_returns_404(self) -> None:
        """访问不存在的 upload 文件返回 404。"""
        self.login_admin()
        response = self.client.get("/uploads/does-not-exist.mp4")
        self.assertEqual(response.status_code, 404)

    def test_generated_real_file_no_auth_returns_401(self) -> None:
        """无鉴权访问真实 generated 文件返回 401。"""
        test_file = C.OUTPUT_DIR / "no-auth-test.png"
        test_content = b"no auth content"
        test_file.write_bytes(test_content)
        try:
            orig = C.GATEWAY_API_KEY
            try:
                C.GATEWAY_API_KEY = "test-key-for-auth"
                response = self.client.get("/generated/no-auth-test.png")
                self.assertIn(response.status_code, (401, 403))
            finally:
                C.GATEWAY_API_KEY = orig
        finally:
            test_file.unlink(missing_ok=True)

    def test_uploads_real_file_no_auth_returns_401(self) -> None:
        """无鉴权访问真实 upload 文件返回 401。"""
        test_file = C.UPLOAD_DIR / "no-auth-test.mp4"
        test_content = b"no auth upload"
        test_file.write_bytes(test_content)
        try:
            orig = C.GATEWAY_API_KEY
            try:
                C.GATEWAY_API_KEY = "test-key-for-auth"
                response = self.client.get("/uploads/no-auth-test.mp4")
                self.assertIn(response.status_code, (401, 403))
            finally:
                C.GATEWAY_API_KEY = orig
        finally:
            test_file.unlink(missing_ok=True)

    def test_path_traversal_blocked(self) -> None:
        """/generated/../xxx 路径穿越被阻止。"""
        self.login_admin()
        response = self.client.get("/generated/../etc/passwd")
        self.assertIn(response.status_code, (400, 403, 404))

    def test_path_traversal_uploads_blocked(self) -> None:
        """/uploads/../xxx 路径穿越被阻止。"""
        self.login_admin()
        response = self.client.get("/uploads/../etc/passwd")
        self.assertIn(response.status_code, (400, 403, 404))

    def test_path_traversal_encoded_blocked(self) -> None:
        """/generated/%2e%2e/xxx URL 编码路径穿越被阻止。"""
        self.login_admin()
        response = self.client.get("/generated/%2e%2e/etc/passwd")
        self.assertIn(response.status_code, (400, 403, 404))

    def test_path_traversal_uploads_encoded_blocked(self) -> None:
        """/uploads/%2e%2e/xxx URL 编码路径穿越被阻止。"""
        self.login_admin()
        response = self.client.get("/uploads/%2e%2e/etc/passwd")
        self.assertIn(response.status_code, (400, 403, 404))

    def test_legacy_assistant_routes_return_404(self) -> None:
        """v0.2.0 移除的 legacy assistant/prompt 普通路由返回 404。"""
        self.login_admin()
        for path in ["/v1/prompt/enhance", "/v1/assistant/plan", "/v1/assistant/generate"]:
            with self.subTest(path=path):
                response = self.client.post(path, json={"prompt": "test"})
                self.assertEqual(response.status_code, 404, response.text)


if __name__ == "__main__":
    unittest.main()
