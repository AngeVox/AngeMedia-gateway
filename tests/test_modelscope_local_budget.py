"""验证本地预算迁移、跨进程争用及上游限流的独立语义。"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.providers.errors import ProviderAuthError, ProviderTimeout, RateLimited
from angemedia_gateway.providers.image.modelscope import ModelScopeProvider
from angemedia_gateway.providers.image.quota import LocalQuota
from angemedia_gateway.repositories.provider_budgets import claim_daily_budget, read_daily_budget
from angemedia_gateway.schemas import ImageRequest
from angemedia_gateway.state import init_db


class ModelScopeBudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="modelscope-local-budget-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.day = date.today().isoformat()
        for name, value in {"DB_FILE": self.root / "test.db", "QUOTA_FILE": self.root / "quota.json", "MODELSCOPE_DAILY_LIMIT": 7, "MODELSCOPE_API_KEY": "TEST_ONLY_NOT_A_REAL_KEY", "POLL_INTERVAL": 0, "MAX_POLL_TIME": 1}.items():
            setting = patch.object(C, name, value)
            setting.start()
            self.addCleanup(setting.stop)
        init_db()

    def state(self, day: str | None = None, limit: int = 7):
        return read_daily_budget("modelscope", day or self.day, limit, C.QUOTA_FILE)

    def claim(self, day: str | None = None, limit: int = 7) -> bool:
        return claim_daily_budget("modelscope", day or self.day, limit, C.QUOTA_FILE)

    def test_legacy_state_is_imported_once_without_changing_the_file(self) -> None:
        original = json.dumps({"day": self.day, "remaining": 2})
        C.QUOTA_FILE.write_text(original, encoding="utf-8")
        self.assertEqual(self.state().remaining, 2)
        self.assertTrue(self.claim())
        self.assertEqual(C.QUOTA_FILE.read_text(encoding="utf-8"), original)
        C.QUOTA_FILE.write_text(json.dumps({"day": self.day, "remaining": 7}), encoding="utf-8")
        self.assertEqual(self.state().remaining, 1)
        init_db()
        self.assertTrue(self.claim())
        self.assertFalse(self.claim())
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='provider_local_budget_v1'").fetchone()[0], 1)

    def test_day_rollover_and_limit_changes_preserve_allocated_attempts(self) -> None:
        old_day = "2000-01-01"
        C.QUOTA_FILE.write_text(json.dumps({"day": old_day, "remaining": 1}), encoding="utf-8")
        self.assertEqual(self.state(old_day).used, 6)
        self.assertEqual(self.state().used, 0)
        self.assertTrue(self.claim())
        self.assertTrue(self.claim())
        # 旧日分配晚于次日操作到达时，也不能重置任一日的已用数。
        self.assertTrue(self.claim(old_day))
        self.assertEqual(self.state(old_day).used, 7)
        self.assertEqual(self.state().used, 2)
        self.assertEqual(self.state(limit=1).remaining, 0)
        self.assertFalse(self.claim(limit=1))
        self.assertEqual(self.state(limit=10).remaining, 8)
        self.assertFalse(self.claim(limit=0))

    def test_invalid_legacy_state_is_conservative_and_does_not_leak_content(self) -> None:
        C.QUOTA_FILE.write_text("PRIVATE_LEGACY_CONTENT", encoding="utf-8")
        with self.assertLogs("angemedia-gateway", level="WARNING") as logged:
            self.assertFalse(self.claim())
        self.assertNotIn("PRIVATE_LEGACY_CONTENT", repr(logged.output))
        self.assertEqual(self.state().remaining, 0)

    def test_six_processes_share_one_atomic_limit(self) -> None:
        code = """
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from angemedia_gateway.repositories.provider_budgets import claim_daily_budget
input()
print(sum(claim_daily_budget('modelscope',sys.argv[3],7,Path(sys.argv[2])) for _ in range(6)))
"""
        env = {**os.environ, "ANGEMEDIA_DB_FILE": str(C.DB_FILE), "PYTHONDONTWRITEBYTECODE": "1"}
        processes = []
        try:
            for _ in range(6):
                processes.append(subprocess.Popen([sys.executable, "-c", code, str(ROOT / "scripts"), str(C.QUOTA_FILE), self.day], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
            for process in processes:
                process.stdin.write("start\n")
                process.stdin.flush()
            results = [process.communicate(timeout=30) for process in processes]
            for process, (_, error) in zip(processes, results):
                self.assertEqual(process.returncode, 0, error)
            self.assertEqual(sum(int(output.strip()) for output, _ in results), 7)
            self.assertEqual(self.state().used, 7)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def run_provider(self, responses: list[httpx.Response | Exception]) -> list[httpx.Request]:
        requests = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("httpx.AsyncClient", return_value=client):
            asyncio.run(ModelScopeProvider().generate(ImageRequest(prompt="测试预算"), RouteTarget("modelscope", "Qwen/Qwen-Image-2512")))
        return requests

    def test_submit_429_does_not_exhaust_the_local_day_or_claim_remote_balance(self) -> None:
        with self.assertRaises(RateLimited) as raised:
            self.run_provider([httpx.Response(429, text="PRIVATE_UPSTREAM_BODY")])
        self.assertEqual(raised.exception.status_code, 429)
        self.assertNotIn("quota is exhausted", str(raised.exception))
        self.assertNotIn("PRIVATE_UPSTREAM_BODY", str(raised.exception))
        self.assertEqual(self.state().remaining, 6)

    def test_poll_429_counts_only_the_single_submit_attempt(self) -> None:
        with self.assertRaises(RateLimited) as raised:
            self.run_provider([httpx.Response(200, json={"task_id": "test-task"}), httpx.Response(429)])
        self.assertIn("poll", str(raised.exception))
        self.assertEqual(self.state().used, 1)

    def test_success_auth_rejection_and_timeout_each_use_one_allocation(self) -> None:
        requests = self.run_provider([httpx.Response(200, json={"task_id": "test-task"}), httpx.Response(200, json={"task_status": "SUCCEED", "output_images": ["https://example.com/result.png"]})])
        self.assertEqual([request.method for request in requests], ["POST", "GET"])
        with self.assertRaises(ProviderAuthError):
            self.run_provider([httpx.Response(403)])
        with self.assertRaises(ProviderTimeout):
            self.run_provider([httpx.ReadTimeout("test timeout")])
        self.assertEqual(self.state().used, 3)

    def test_local_exhaustion_stops_http_and_health_is_explicit_about_units(self) -> None:
        for _ in range(7):
            self.assertTrue(self.claim())
        with self.assertRaises(RateLimited) as raised:
            self.run_provider([])
        self.assertEqual(raised.exception.error_category, "local_budget")
        self.assertFalse(raised.exception.retryable)
        health = ModelScopeProvider().health()
        self.assertEqual(health["remaining_local_counter"], 0)
        self.assertEqual(health["local_budget"]["unit"], "submission_attempt")
        self.assertIsNone(health["local_budget"]["upstream_balance"])
        self.assertEqual(LocalQuota().snapshot().remaining, 0)
