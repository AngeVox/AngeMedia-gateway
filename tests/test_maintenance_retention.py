"""Admin maintenance retention cleanup contracts."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

from fastapi.testclient import TestClient  # noqa: E402

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.helpers import safe_json  # noqa: E402
from angemedia_gateway.queue.contracts import QueueDispatchEnvelope  # noqa: E402
from angemedia_gateway.queue.settings import WORKER_TASK_NAME  # noqa: E402
from angemedia_gateway.repositories.assistant_sessions import (  # noqa: E402
    add_assistant_message,
    add_assistant_run,
    create_assistant_session,
)
from angemedia_gateway.repositories.job_attempts import create_job_attempt  # noqa: E402
from angemedia_gateway.repositories.job_dispatches import create_job_dispatch  # noqa: E402
from angemedia_gateway.repositories.job_events import append_job_event  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.services.maintenance_retention import CONFIRM_PHRASE  # noqa: E402
from angemedia_gateway.state import (  # noqa: E402
    create_gateway_api_key,
    create_job,
    ensure_default_admin_user,
    init_db,
    record_generation,
    save_asset,
)


class MaintenanceRetentionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="maintenance-retention-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._output_dir = Path(self._tmp_dir) / "output"
        self._upload_dir = Path(self._tmp_dir) / "upload"
        self._output_dir.mkdir()
        self._upload_dir.mkdir()

        self._orig_db = C.DB_FILE
        self._orig_output = C.OUTPUT_DIR
        self._orig_upload = C.UPLOAD_DIR
        self._orig_gateway_key = C.GATEWAY_API_KEY
        C.DB_FILE = self._db_path
        C.OUTPUT_DIR = self._output_dir
        C.UPLOAD_DIR = self._upload_dir
        C.GATEWAY_API_KEY = ""
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        C.DB_FILE = self._orig_db
        C.OUTPUT_DIR = self._orig_output
        C.UPLOAD_DIR = self._orig_upload
        C.GATEWAY_API_KEY = self._orig_gateway_key
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def login_admin(self) -> None:
        response = self.client.post("/v1/admin/login", json={"username": "admin", "password": "admin123456"})
        self.assertEqual(response.status_code, 200, response.text)

    def gateway_headers(self) -> dict[str, str]:
        item = create_gateway_api_key(name="maintenance-denied")
        return {"Authorization": f"Bearer {item['key']}"}

    def test_retention_preview_requires_admin_session_and_rejects_gateway_key(self) -> None:
        unauthenticated = self.client.post("/v1/admin/maintenance/retention/preview", json={})
        denied = self.client.post(
            "/v1/admin/maintenance/retention/preview",
            json={},
            headers=self.gateway_headers(),
        )
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(denied.status_code, 403, denied.text)

    def test_retention_preview_counts_old_terminal_jobs_and_assistant_records(self) -> None:
        self._seed_records()
        self.login_admin()

        response = self.client.post(
            "/v1/admin/maintenance/retention/preview",
            json={"older_than_days": 30, "limit": 100},
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]

        self.assertEqual(data["jobs"]["jobs"], 1)
        self.assertEqual(data["jobs"]["events"], 1)
        self.assertEqual(data["jobs"]["attempts"], 1)
        self.assertEqual(data["jobs"]["dispatches"], 1)
        self.assertEqual(data["jobs"]["assets_to_unlink"], 1)
        self.assertEqual(data["jobs"]["generations_to_unlink"], 1)
        self.assertEqual(data["assistant"]["sessions"], 1)
        self.assertEqual(data["assistant"]["messages"], 2)
        self.assertEqual(data["assistant"]["runs"], 1)
        self.assertEqual(data["assets_deleted"], 0)
        self.assertEqual(data["media_files_deleted"], 0)
        self.assertNotIn("input_json", response.text)
        self.assertNotIn("request_hash", response.text)
        self.assertNotIn("sk-leaked", response.text)

    def test_retention_clean_requires_double_confirmation(self) -> None:
        self.login_admin()
        response = self.client.post(
            "/v1/admin/maintenance/retention/clean",
            json={"older_than_days": 30, "confirm": "CLEAN"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["error"], "confirm_required")

    def test_retention_clean_deletes_records_without_deleting_assets_or_active_jobs(self) -> None:
        seeded = self._seed_records()
        self.login_admin()

        response = self.client.post(
            "/v1/admin/maintenance/retention/clean",
            json={"older_than_days": 30, "limit": 100, "confirm": CONFIRM_PHRASE},
        )
        self.assertEqual(response.status_code, 200, response.text)
        deleted = response.json()["data"]["deleted"]
        self.assertEqual(deleted["deleted_jobs"], 1)
        self.assertEqual(deleted["deleted_events"], 1)
        self.assertEqual(deleted["deleted_attempts"], 1)
        self.assertEqual(deleted["deleted_dispatches"], 1)
        self.assertEqual(deleted["unlinked_assets"], 1)
        self.assertEqual(deleted["unlinked_generations"], 1)
        self.assertEqual(deleted["deleted_assistant_sessions"], 1)
        self.assertEqual(deleted["deleted_assistant_messages"], 2)
        self.assertEqual(deleted["deleted_assistant_runs"], 1)
        self.assertEqual(deleted["assets_deleted"], 0)
        self.assertEqual(deleted["media_files_deleted"], 0)

        with sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            self.assertIsNone(conn.execute("SELECT id FROM jobs WHERE id=?", (seeded["old_job"],)).fetchone())
            self.assertIsNotNone(conn.execute("SELECT id FROM jobs WHERE id=?", (seeded["queued_job"],)).fetchone())
            self.assertIsNotNone(conn.execute("SELECT id FROM jobs WHERE id=?", (seeded["recent_job"],)).fetchone())
            asset = conn.execute("SELECT job_id FROM assets WHERE id='asset-old'").fetchone()
            generation = conn.execute("SELECT job_id FROM generations WHERE id='gen-old'").fetchone()
            self.assertIsNotNone(asset)
            self.assertIsNone(asset["job_id"])
            self.assertIsNotNone(generation)
            self.assertIsNone(generation["job_id"])
            self.assertIsNotNone(conn.execute("SELECT id FROM assistant_sessions WHERE id='session-recent'").fetchone())
            self.assertIsNone(conn.execute("SELECT id FROM assistant_sessions WHERE id='session-old'").fetchone())

    def test_diagnostics_summary_includes_safe_maintenance_preview(self) -> None:
        self._seed_records()
        self.login_admin()

        response = self.client.get("/v1/admin/diagnostics/summary")
        self.assertEqual(response.status_code, 200, response.text)
        maintenance = response.json()["data"]["maintenance"]
        self.assertEqual(maintenance["state"], "ok")
        self.assertEqual(maintenance["jobs"]["jobs"], 1)
        self.assertEqual(maintenance["assistant"]["sessions"], 1)
        self.assertEqual(maintenance["assets_deleted"], 0)
        self.assertNotIn("input_json", response.text)
        self.assertNotIn("output_json", response.text)
        self.assertNotIn("sk-leaked", response.text)

    def _seed_records(self) -> dict[str, str]:
        old = "2026-01-01T00:00:00+00:00"
        recent = "2099-01-01T00:00:00+00:00"
        old_job = create_job(
            job_id="old-terminal-job",
            kind="image",
            status="succeeded",
            provider="agnes_image",
            model="agnes-image-2.1-flash",
            prompt="safe prompt",
            input_json=safe_json({"api_key": "sk-leaked", "request_hash": "leak"}),
            output_json=safe_json({"provider_raw_body": "secret"}),
        )
        queued_job = create_job(
            job_id="old-queued-job",
            kind="video",
            status="queued",
            prompt="active",
        )
        recent_job = create_job(
            job_id="recent-terminal-job",
            kind="image",
            status="failed",
            prompt="recent",
        )
        append_job_event(old_job["id"], "completed", {"provider_raw_body": "secret"}, stage="finalize")
        create_job_attempt(
            job_id=old_job["id"],
            attempt_number=1,
            stage="image_generate",
            status="succeeded",
            completed_at=old,
            detail={"api_key": "sk-leaked"},
        )
        create_job_dispatch(
            job_id=old_job["id"],
            topic=WORKER_TASK_NAME,
            payload=QueueDispatchEnvelope(
                job_id=old_job["id"],
                job_kind="image",
                stage="image_generate",
                payload_schema_version=1,
                attempt=1,
            ).as_dict(),
            available_at=old,
        )
        save_asset(
            id="asset-old",
            filename="image.png",
            storage_area="output",
            relative_path="generated/image.png",
            url_path="/generated/image.png",
            media_type="image",
            source="generated",
            size=123,
            prompt="safe",
            model="agnes-image-2.1-flash",
            provider="agnes_image",
            job_id=old_job["id"],
        )
        record_generation(
            media_type="image",
            prompt="safe",
            enhanced_prompt=None,
            model="agnes-image-2.1-flash",
            status="succeeded",
            result={"url": "/generated/image.png"},
            provider="agnes_image",
            job_id=old_job["id"],
        )
        create_assistant_session("session-old", "old chat")
        add_assistant_message("message-old-user", "session-old", "user", "AngeMedia Jobs 怎么看", {"api_key": "sk-leaked"})
        add_assistant_message("message-old-assistant", "session-old", "assistant", "safe answer", {})
        add_assistant_run(
            "run-old",
            "session-old",
            "succeeded",
            "angemedia_faq",
            {"message": "safe"},
            {"answer": "safe"},
            [{"tool": "local_kb_search"}],
        )
        create_assistant_session("session-recent", "recent chat")
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                "UPDATE jobs SET created_at=?, updated_at=? WHERE id IN (?, ?)",
                (old, old, old_job["id"], queued_job["id"]),
            )
            conn.execute(
                "UPDATE jobs SET created_at=?, updated_at=? WHERE id=?",
                (recent, recent, recent_job["id"]),
            )
            conn.execute("UPDATE job_events SET created_at=? WHERE job_id=?", (old, old_job["id"]))
            conn.execute(
                "UPDATE job_attempts SET started_at=?, completed_at=? WHERE job_id=?",
                (old, old, old_job["id"]),
            )
            conn.execute(
                "UPDATE job_dispatches SET available_at=?, created_at=?, updated_at=? WHERE job_id=?",
                (old, old, old, old_job["id"]),
            )
            conn.execute(
                "UPDATE generations SET id='gen-old', created_at=?, updated_at=?, started_at=?, completed_at=? WHERE job_id=?",
                (old, old, old, old, old_job["id"]),
            )
            conn.execute("UPDATE assistant_sessions SET created_at=?, updated_at=? WHERE id='session-old'", (old, old))
            conn.execute("UPDATE assistant_messages SET created_at=? WHERE session_id='session-old'", (old,))
            conn.execute(
                "UPDATE assistant_runs SET created_at=?, completed_at=? WHERE session_id='session-old'",
                (old, old),
            )
            conn.execute("UPDATE assistant_sessions SET created_at=?, updated_at=? WHERE id='session-recent'", (recent, recent))
        return {
            "old_job": old_job["id"],
            "queued_job": queued_job["id"],
            "recent_job": recent_job["id"],
        }


if __name__ == "__main__":
    unittest.main()
