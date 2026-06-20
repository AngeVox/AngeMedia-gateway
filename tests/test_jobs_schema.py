"""jobs 表 schema 测试。"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.state import init_db


class _JobsSchemaTestBase(unittest.TestCase):
    """共享 setUp/tearDown：独立临时目录 + 临时 DB。"""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="jobs-schema-test-")
        self.db_path = Path(self._tmp_dir) / "test.db"
        import angemedia_gateway.config as C
        self._orig_db = C.DB_FILE
        self._config_mod = C
        C.DB_FILE = self.db_path
        init_db()

    def tearDown(self) -> None:
        self._config_mod.DB_FILE = self._orig_db
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn


# ── 1-3. 表存在、字段、迁移 ────────────────────────────

class JobsTableStructureTest(_JobsSchemaTestBase):
    """jobs 表结构和迁移记录。"""

    def test_table_exists(self) -> None:
        """init_db() 创建 jobs 表。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_table_has_expected_columns(self) -> None:
        """jobs 表包含所有预期字段。"""
        conn = self._conn()
        try:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            expected = {
                "id", "kind", "status", "provider", "model", "prompt",
                "input_json", "output_json", "error_code", "error_message",
                "external_task_id", "created_at", "updated_at",
                "started_at", "completed_at", "duration_ms",
                "request_hash", "request_hash_version",
                "error_category", "human_hint", "retryable", "gateway_stage",
                "stage", "payload_schema_version", "priority", "scheduled_at",
                "next_retry_at", "attempt_count", "max_attempts", "claim_token",
                "claim_expires_at", "worker_kind", "provider_status",
                "cancel_requested_at", "version",
            }
            self.assertEqual(cols, expected)
        finally:
            conn.close()

    def test_migration_record_exists(self) -> None:
        """schema_migrations 包含 jobs_v1 记录。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM schema_migrations WHERE version = 'jobs_v1'"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIn("T", row["applied_at"])
        finally:
            conn.close()

    def test_request_hash_migration_record_exists(self) -> None:
        """schema_migrations 包含 jobs_request_hash_v1 记录。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM schema_migrations WHERE version = 'jobs_request_hash_v1'"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIn("T", row["applied_at"])
        finally:
            conn.close()

    def test_request_hash_index_exists(self) -> None:
        """jobs request_hash 短窗口查询索引存在。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_jobs_kind_request_hash_created_at'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()


# ── 4. 幂等性 ──────────────────────────────────────────

class JobsInitDbIdempotentTest(_JobsSchemaTestBase):
    """init_db() 重复运行幂等。"""

    def test_second_call_no_error(self) -> None:
        """第二次调用 init_db() 不报错。"""
        init_db()

    def test_tables_preserved_after_second_call(self) -> None:
        """第二次 init_db() 后 jobs 表仍存在。"""
        init_db()
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_migration_not_duplicated(self) -> None:
        """重复 init_db() 后 jobs_v1 仍只有一条。"""
        init_db()
        init_db()
        conn = self._conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = 'jobs_v1'"
            ).fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn.close()

    def test_request_hash_migration_not_duplicated(self) -> None:
        """重复 init_db() 后 jobs_request_hash_v1 仍只有一条。"""
        init_db()
        init_db()
        conn = self._conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = 'jobs_request_hash_v1'"
            ).fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn.close()

    def test_legacy_jobs_table_gets_request_hash_columns(self) -> None:
        """已有 DB 的 legacy jobs 表可补齐 request_hash 字段与索引。"""
        conn = self._conn()
        try:
            conn.execute("DROP TABLE jobs")
            conn.execute(
                """
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK(kind IN ('image', 'video')),
                    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')),
                    provider TEXT,
                    model TEXT,
                    prompt TEXT,
                    input_json TEXT,
                    output_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    external_task_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    duration_ms INTEGER
                )
                """
            )
        finally:
            conn.close()

        init_db()

        conn = self._conn()
        try:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            self.assertIn("request_hash", cols)
            self.assertIn("request_hash_version", cols)
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_jobs_kind_request_hash_created_at'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()


# ── 5-6. 合法插入 ──────────────────────────────────────

class JobsInsertTest(_JobsSchemaTestBase):
    """合法 job 可以插入。"""

    def test_insert_image_job(self) -> None:
        """可插入合法 image job。"""
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO jobs(id, kind, status, created_at, updated_at) "
                "VALUES('img-001', 'image', 'succeeded', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
            )
            row = conn.execute("SELECT kind, status FROM jobs WHERE id = 'img-001'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["kind"], "image")
            self.assertEqual(row["status"], "succeeded")
        finally:
            conn.close()

    def test_insert_video_job(self) -> None:
        """可插入合法 video job。"""
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO jobs(id, kind, status, created_at, updated_at) "
                "VALUES('vid-001', 'video', 'queued', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
            )
            row = conn.execute("SELECT kind, status FROM jobs WHERE id = 'vid-001'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["kind"], "video")
            self.assertEqual(row["status"], "queued")
        finally:
            conn.close()


# ── 7-8. CHECK 约束 ───────────────────────────────────

class JobsCheckConstraintTest(_JobsSchemaTestBase):
    """CHECK 约束拒绝非法值。"""

    def test_illegal_kind_rejected(self) -> None:
        """非法 kind 被 CHECK 拒绝。"""
        conn = self._conn()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO jobs(id, kind, status, created_at, updated_at) "
                    "VALUES('bad-001', 'audio', 'queued', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
                )
        finally:
            conn.close()

    def test_illegal_status_rejected(self) -> None:
        """非法 status 被 CHECK 拒绝。"""
        conn = self._conn()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO jobs(id, kind, status, created_at, updated_at) "
                    "VALUES('bad-002', 'image', 'unknown', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
                )
        finally:
            conn.close()


# ── 9. nullable 字段 ───────────────────────────────────

class JobsNullableTest(_JobsSchemaTestBase):
    """nullable 字段可以为空。"""

    def test_nullable_fields_can_be_null(self) -> None:
        """可插入只含必填字段的 job，nullable 字段为 NULL。"""
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO jobs(id, kind, status, created_at, updated_at) "
                "VALUES('null-001', 'image', 'queued', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
            )
            row = conn.execute("SELECT * FROM jobs WHERE id = 'null-001'").fetchone()
            self.assertIsNotNone(row)
            self.assertIsNone(row["provider"])
            self.assertIsNone(row["model"])
            self.assertIsNone(row["prompt"])
            self.assertIsNone(row["input_json"])
            self.assertIsNone(row["output_json"])
            self.assertIsNone(row["error_code"])
            self.assertIsNone(row["error_message"])
            self.assertIsNone(row["external_task_id"])
            self.assertIsNone(row["started_at"])
            self.assertIsNone(row["completed_at"])
            self.assertIsNone(row["duration_ms"])
            self.assertIsNone(row["request_hash"])
            self.assertIsNone(row["request_hash_version"])
        finally:
            conn.close()


# ── 10-12. 不存在的字段 ───────────────────────────────

class JobsForbiddenFieldTest(_JobsSchemaTestBase):
    """jobs 表不包含禁止字段。"""

    def test_no_asset_id_field(self) -> None:
        """不存在 asset_id 字段。"""
        conn = self._conn()
        try:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            self.assertNotIn("asset_id", cols)
        finally:
            conn.close()

    def test_no_generation_id_field(self) -> None:
        """不存在 generation_id 字段。"""
        conn = self._conn()
        try:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            self.assertNotIn("generation_id", cols)
        finally:
            conn.close()

    def test_no_local_path_field(self) -> None:
        """不存在 local_path 字段。"""
        conn = self._conn()
        try:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            self.assertNotIn("local_path", cols)
        finally:
            conn.close()


# ── 13. 现有核心表仍存在 ──────────────────────────────

class ExistingTablesIntactTest(_JobsSchemaTestBase):
    """init_db() 后现有核心表仍存在。"""

    def test_generations_table_exists(self) -> None:
        """generations 表仍存在。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='generations'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_video_tasks_table_exists(self) -> None:
        """video_tasks 表仍存在。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='video_tasks'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_assets_table_exists(self) -> None:
        """assets 表仍存在。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='assets'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_gateway_api_keys_table_exists(self) -> None:
        """gateway_api_keys 表仍存在。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='gateway_api_keys'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_all_migration_markers_preserved(self) -> None:
        """所有 migration marker 仍存在。"""
        conn = self._conn()
        try:
            markers = {
                row["version"] for row in conn.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            self.assertIn("baseline", markers)
            self.assertIn("gateway_api_keys_v1", markers)
            self.assertIn("jobs_v1", markers)
            self.assertIn("jobs_request_hash_v1", markers)
        finally:
            conn.close()
