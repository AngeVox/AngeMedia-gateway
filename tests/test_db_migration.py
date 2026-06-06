"""DB 基础设施最小落地测试：schema_migrations + PRAGMA + 重复 init_db。"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.state import init_db


class _DbTestBase(unittest.TestCase):
    """共享 setUp/tearDown：独立临时目录 + 临时 DB，WAL sidecar 文件一并清理。"""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="db-migration-test-")
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


# ── 1. schema_migrations 表 ──────────────────────────

class SchemaMigrationsTableTest(_DbTestBase):
    """schema_migrations 表存在且有 baseline 记录。"""

    def test_table_exists(self) -> None:
        """init_db() 创建 schema_migrations 表。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_baseline_record_exists(self) -> None:
        """schema_migrations 至少包含 baseline 记录。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM schema_migrations WHERE version = 'baseline'"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIn("T", row["applied_at"])
        finally:
            conn.close()

    def test_baseline_only_one_row(self) -> None:
        """baseline 记录不重复。"""
        conn = self._conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = 'baseline'"
            ).fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn.close()


# ── 2. 重复执行 ──────────────────────────────────────

class InitDbIdempotentTest(_DbTestBase):
    """init_db() 可重复执行。"""

    def test_second_call_no_error(self) -> None:
        """第二次调用 init_db() 不报错。"""
        init_db()

    def test_tables_preserved_after_second_call(self) -> None:
        """第二次 init_db() 后所有表仍存在。"""
        init_db()
        conn = self._conn()
        try:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            expected = {
                "config", "generations", "video_tasks", "uploads",
                "assistant_plans", "custom_providers", "admin_users",
                "admin_sessions", "admin_login_attempts", "assets",
                "schema_migrations",
            }
            self.assertTrue(expected.issubset(tables))
        finally:
            conn.close()

    def test_baseline_not_duplicated(self) -> None:
        """重复 init_db() 后 baseline 仍只有一条。"""
        init_db()
        init_db()
        conn = self._conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = 'baseline'"
            ).fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn.close()


# ── 3. PRAGMA 验证 ───────────────────────────────────

class PragmaTest(_DbTestBase):
    """db_connect() 设置的 PRAGMA 值。"""

    def test_foreign_keys_enabled(self) -> None:
        """foreign_keys 已启用（per-connection 设置，通过 db_connect() 验证）。"""
        from angemedia_gateway.state import db_connect
        with closing(db_connect()) as conn:
            val = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            self.assertEqual(val, 1)

    def test_busy_timeout_set(self) -> None:
        """busy_timeout 已设置为 5000ms（通过 db_connect() 验证）。"""
        from angemedia_gateway.state import db_connect
        with closing(db_connect()) as conn:
            val = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            self.assertEqual(val, 5000)

    def test_wal_mode_on_file_db(self) -> None:
        """文件型 DB 上 WAL 可启用。

        注意：内存数据库不支持 WAL，但当前所有场景均为文件 DB。
        """
        conn = self._conn()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode, "wal")
        finally:
            conn.close()


# ── 4. 现有表完整性 ──────────────────────────────────

class ExistingTablesIntactTest(_DbTestBase):
    """init_db() 后现有表结构不变。"""

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

    def test_assets_has_14_columns(self) -> None:
        """assets 表仍为 14 个字段（含 job_id）。"""
        conn = self._conn()
        try:
            cols = conn.execute("PRAGMA table_info(assets)").fetchall()
            self.assertEqual(len(cols), 14)
        finally:
            conn.close()

    def test_config_table_exists(self) -> None:
        """config 表仍存在。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='config'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_admin_users_table_exists(self) -> None:
        """admin_users 表仍存在。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='admin_users'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_admin_sessions_table_exists(self) -> None:
        """admin_sessions 表仍存在。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='admin_sessions'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_custom_providers_table_exists(self) -> None:
        """custom_providers 表仍存在。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='custom_providers'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

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

    def test_uploads_table_exists(self) -> None:
        """uploads 表仍存在。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='uploads'"
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

    def test_admin_login_attempts_table_exists(self) -> None:
        """admin_login_attempts 表仍存在。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='admin_login_attempts'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_assistant_plans_table_exists(self) -> None:
        """assistant_plans 表仍存在。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='assistant_plans'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()
