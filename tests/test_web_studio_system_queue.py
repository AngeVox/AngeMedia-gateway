"""Web Studio system queue runtime controls."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "app" / "www" / "assets" / "studio"


class WebStudioSystemQueueContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = (STUDIO / "app.js").read_text(encoding="utf-8")
        cls.layout = (STUDIO / "layout.js").read_text(encoding="utf-8")
        cls.page = (STUDIO / "features" / "system" / "page.js").read_text(encoding="utf-8")
        cls.i18n = (STUDIO / "i18n.js").read_text(encoding="utf-8")

    def test_system_route_and_navigation_are_exposed(self) -> None:
        self.assertIn("#/system", self.app)
        self.assertIn("#/system", self.layout)
        self.assertIn("nav.system", self.layout)

    def test_queue_runtime_uses_dedicated_admin_api_and_confirmation(self) -> None:
        self.assertIn("/admin/system/queue", self.page)
        self.assertIn("/admin/system/queue/redis/detect", self.page)
        self.assertIn("/admin/system/queue/switch", self.page)
        self.assertIn("confirmModal", self.page)
        self.assertIn("redis_url", self.page)

    def test_redis_credentials_are_write_only_in_studio(self) -> None:
        self.assertIn("type: 'password'", self.page)
        self.assertNotIn("summary.redis_url", self.page)
        self.assertNotIn("data.redis_url", self.page)
        self.assertIn("不会从 API 回显", self.i18n)

    def test_copy_explains_safe_switching_and_local_default(self) -> None:
        self.assertIn("默认 Local Queue", self.i18n)
        self.assertIn("queued/running", self.i18n)
        self.assertIn("real PING", self.i18n)

    def test_runtime_status_renders_process_redis_and_dispatch_values(self) -> None:
        for token in ("system.dispatcher", "system.worker", "item.host", "item.port", "summary.active_dispatches"):
            self.assertIn(token, self.page)
        self.assertNotIn("return `  ·  `", self.page)
        self.assertNotIn("meta: ` `", self.page)


if __name__ == "__main__":
    unittest.main()
