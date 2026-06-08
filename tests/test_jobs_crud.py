"""jobs 表 CRUD helper 测试。"""
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


class _JobsCrudTestBase(unittest.TestCase):
    """共享 setUp/tearDown：独立临时目录 + 临时 DB。"""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="jobs-crud-test-")
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


# ── 1-4. create_job ────────────────────────────────────

class CreateJobTest(_JobsCrudTestBase):
    """create_job 函数。"""

    def test_create_queued_image_job(self) -> None:
        """创建 queued image job。"""
        from angemedia_gateway.state import create_job
        job = create_job(kind="image", status="queued", prompt="test")
        self.assertEqual(job["kind"], "image")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["prompt"], "test")

    def test_create_queued_video_job(self) -> None:
        """创建 queued video job。"""
        from angemedia_gateway.state import create_job
        job = create_job(kind="video", status="queued", prompt="video test")
        self.assertEqual(job["kind"], "video")
        self.assertEqual(job["status"], "queued")

    def test_returns_all_expected_fields(self) -> None:
        """返回值包含所有 jobs 字段。"""
        from angemedia_gateway.state import create_job
        job = create_job(kind="image")
        expected = {
            "id", "kind", "status", "provider", "model", "prompt",
            "input_json", "output_json", "error_code", "error_message",
            "external_task_id", "created_at", "updated_at",
            "started_at", "completed_at", "duration_ms",
            "request_hash", "request_hash_version",
        }
        self.assertEqual(set(job.keys()), expected)

    def test_old_call_defaults_request_hash_fields_to_null(self) -> None:
        """旧 create_job 调用不需要传 request_hash。"""
        from angemedia_gateway.state import create_job
        job = create_job(kind="image")
        self.assertIsNone(job["request_hash"])
        self.assertIsNone(job["request_hash_version"])

    def test_can_store_request_hash_fields(self) -> None:
        """create_job 可选写入 request_hash / request_hash_version。"""
        from angemedia_gateway.state import create_job, get_job, list_jobs
        job = create_job(
            kind="image",
            request_hash="a" * 64,
            request_hash_version=1,
        )
        self.assertEqual(job["request_hash"], "a" * 64)
        self.assertEqual(job["request_hash_version"], 1)
        fetched = get_job(job["id"])
        self.assertEqual(fetched["request_hash"], "a" * 64)
        listed = list_jobs()[0]
        self.assertEqual(listed["request_hash"], "a" * 64)

    def test_created_at_and_updated_at_auto_populated(self) -> None:
        """created_at / updated_at 自动写入。"""
        from angemedia_gateway.state import create_job
        job = create_job(kind="image")
        self.assertIsNotNone(job["created_at"])
        self.assertIn("T", job["created_at"])
        self.assertEqual(job["created_at"], job["updated_at"])


# ── 5-6. get_job ───────────────────────────────────────

class GetJobTest(_JobsCrudTestBase):
    """get_job 函数。"""

    def test_get_existing_job(self) -> None:
        """可查询已创建 job。"""
        from angemedia_gateway.state import create_job, get_job
        created = create_job(kind="image", prompt="get-test")
        fetched = get_job(created["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], created["id"])
        self.assertEqual(fetched["prompt"], "get-test")

    def test_get_nonexistent_returns_none(self) -> None:
        """对不存在 ID 返回 None。"""
        from angemedia_gateway.state import get_job
        self.assertIsNone(get_job("nonexistent-id"))


# ── 7-10. list_jobs ────────────────────────────────────

class ListJobsTest(_JobsCrudTestBase):
    """list_jobs 函数。"""

    def test_default_returns_recent_jobs(self) -> None:
        """默认返回最近创建的 job。"""
        from angemedia_gateway.state import create_job, list_jobs
        j1 = create_job(kind="image", prompt="first")
        j2 = create_job(kind="image", prompt="second")
        jobs = list_jobs()
        self.assertGreaterEqual(len(jobs), 2)
        # 最新的在前
        self.assertEqual(jobs[0]["id"], j2["id"])
        self.assertEqual(jobs[1]["id"], j1["id"])

    def test_filter_by_kind(self) -> None:
        """list_jobs(kind='image') 只返回 image。"""
        from angemedia_gateway.state import create_job, list_jobs
        create_job(kind="image")
        create_job(kind="video")
        image_jobs = list_jobs(kind="image")
        for job in image_jobs:
            self.assertEqual(job["kind"], "image")

    def test_filter_by_status(self) -> None:
        """list_jobs(status='queued') 只返回 queued。"""
        from angemedia_gateway.state import create_job, update_job_status, list_jobs
        j1 = create_job(kind="image", status="queued")
        j2 = create_job(kind="image", status="queued")
        update_job_status(j2["id"], status="running")
        queued = list_jobs(status="queued")
        ids = {j["id"] for j in queued}
        self.assertIn(j1["id"], ids)
        self.assertNotIn(j2["id"], ids)

    def test_limit_and_offset(self) -> None:
        """list_jobs(limit, offset) 行为正确。"""
        from angemedia_gateway.state import create_job, list_jobs
        for i in range(5):
            create_job(kind="image", prompt=f"job-{i}")
        page1 = list_jobs(limit=2, offset=0)
        page2 = list_jobs(limit=2, offset=2)
        self.assertEqual(len(page1), 2)
        self.assertEqual(len(page2), 2)
        self.assertNotEqual(page1[0]["id"], page2[0]["id"])


# ── 11-14. update_job_status ───────────────────────────

class UpdateJobStatusTest(_JobsCrudTestBase):
    """update_job_status 函数。"""

    def test_update_queued_to_running(self) -> None:
        """可将 queued 更新为 running。"""
        from angemedia_gateway.state import create_job, update_job_status, get_job
        job = create_job(kind="image", status="queued")
        updated = update_job_status(job["id"], status="running")
        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "running")

    def test_update_running_to_succeeded_with_output(self) -> None:
        """可将 running 更新为 succeeded，并写入 output_json / completed_at / duration_ms。"""
        from angemedia_gateway.state import create_job, update_job_status
        job = create_job(kind="image", status="queued")
        update_job_status(job["id"], status="running", started_at="2026-01-01T00:00:00")
        updated = update_job_status(
            job["id"],
            status="succeeded",
            output_json='{"url":"http://example.com/img.png"}',
            completed_at="2026-01-01T00:00:05",
            duration_ms=5000,
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "succeeded")
        self.assertEqual(updated["output_json"], '{"url":"http://example.com/img.png"}')
        self.assertEqual(updated["completed_at"], "2026-01-01T00:00:05")
        self.assertEqual(updated["duration_ms"], 5000)

    def test_update_running_to_failed_with_error(self) -> None:
        """可将 running 更新为 failed，并写入 error_code / error_message。"""
        from angemedia_gateway.state import create_job, update_job_status
        job = create_job(kind="image", status="queued")
        update_job_status(job["id"], status="running")
        updated = update_job_status(
            job["id"],
            status="failed",
            error_code="rate_limited",
            error_message="All providers rate limited",
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "failed")
        self.assertEqual(updated["error_code"], "rate_limited")
        self.assertEqual(updated["error_message"], "All providers rate limited")

    def test_update_nonexistent_returns_none(self) -> None:
        """更新不存在 job 返回 None。"""
        from angemedia_gateway.state import update_job_status
        self.assertIsNone(update_job_status("fake-id", status="running"))


# ── 15-17. CHECK 约束 ─────────────────────────────────

class JobsCheckConstraintCrudTest(_JobsCrudTestBase):
    """CHECK 约束在 CRUD 层的行为。"""

    def test_illegal_kind_create_fails(self) -> None:
        """非法 kind 创建失败。"""
        from angemedia_gateway.state import create_job
        with self.assertRaises(sqlite3.IntegrityError):
            create_job(kind="audio")

    def test_illegal_status_create_fails(self) -> None:
        """非法 status 创建失败。"""
        from angemedia_gateway.state import create_job
        with self.assertRaises(sqlite3.IntegrityError):
            create_job(kind="image", status="unknown")

    def test_illegal_status_update_fails(self) -> None:
        """非法 status 更新失败。"""
        from angemedia_gateway.state import create_job, update_job_status
        job = create_job(kind="image", status="queued")
        with self.assertRaises(sqlite3.IntegrityError):
            update_job_status(job["id"], status="invalid_status")


# ── 18. updated_at 变化 ────────────────────────────────

class UpdatedAtTest(_JobsCrudTestBase):
    """updated_at 在更新后变化。"""

    def test_updated_at_changes_after_update(self) -> None:
        """updated_at 在更新后变化或至少不早于原值。"""
        from angemedia_gateway.state import create_job, update_job_status, get_job
        job = create_job(kind="image")
        original_updated = job["updated_at"]
        updated = update_job_status(job["id"], status="running")
        self.assertGreaterEqual(updated["updated_at"], original_updated)


# ── 19-20. 禁止字段 ───────────────────────────────────

class JobsForbiddenFieldCrudTest(_JobsCrudTestBase):
    """CRUD 返回值不包含禁止字段。"""

    def test_no_unknown_fields_in_return(self) -> None:
        """不允许更新未知字段（返回值不变）。"""
        from angemedia_gateway.state import create_job, get_job
        job = create_job(kind="image")
        fetched = get_job(job["id"])
        self.assertIsNotNone(fetched)
        # 不应有未知字段
        self.assertNotIn("local_path", fetched)
        self.assertNotIn("asset_id", fetched)
        self.assertNotIn("generation_id", fetched)
        self.assertNotIn("user_id", fetched)
        self.assertNotIn("workspace_id", fetched)
        self.assertNotIn("queue_name", fetched)
        self.assertNotIn("worker_id", fetched)
        self.assertNotIn("retry_count", fetched)
        self.assertNotIn("priority", fetched)

    def test_no_local_path_asset_id_generation_id_in_any_operation(self) -> None:
        """所有 CRUD 操作不涉及 local_path / asset_id / generation_id。"""
        from angemedia_gateway.state import create_job, get_job, list_jobs, update_job_status
        job = create_job(kind="image")
        for op_result in [job, get_job(job["id"]), list_jobs()[0]]:
            self.assertNotIn("local_path", op_result)
            self.assertNotIn("asset_id", op_result)
            self.assertNotIn("generation_id", op_result)
        updated = update_job_status(job["id"], status="running")
        self.assertNotIn("local_path", updated)
        self.assertNotIn("asset_id", updated)
        self.assertNotIn("generation_id", updated)


# ── fail_job ───────────────────────────────────────────

class FailJobTest(_JobsCrudTestBase):
    """fail_job 函数。"""

    def test_fail_job_sets_failed_status(self) -> None:
        """fail_job 标记 job 为 failed。"""
        from angemedia_gateway.state import create_job, fail_job
        job = create_job(kind="image", status="running")
        result = fail_job(job["id"], "provider_error", "all providers failed")
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "provider_error")
        self.assertEqual(result["error_message"], "all providers failed")
