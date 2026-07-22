"""NAS package administrator credential reset contracts."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.cli.reset_admin import reset_admin_credentials  # noqa: E402
from angemedia_gateway.db.schema import init_db  # noqa: E402
from angemedia_gateway.repositories.admin_auth import (  # noqa: E402
    create_admin_session,
    ensure_default_admin_user,
    record_admin_login_failure,
    verify_admin_login,
)
from angemedia_gateway.db.connection import db_connect  # noqa: E402


class AdminResetCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="admin-reset-cli-"))
        self.original_db = C.DB_FILE
        self.original_env = (os.environ.get("ADMIN_USERNAME"), os.environ.get("ADMIN_DEFAULT_PASSWORD"))
        C.DB_FILE = self.root / "state" / "angemedia.db"
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_DEFAULT_PASSWORD"] = "old-password"
        init_db()
        ensure_default_admin_user()
        create_admin_session("admin")
        record_admin_login_failure("admin", "192.0.2.1")

    def tearDown(self) -> None:
        C.DB_FILE = self.original_db
        old_user, old_password = self.original_env
        if old_user is None:
            os.environ.pop("ADMIN_USERNAME", None)
        else:
            os.environ["ADMIN_USERNAME"] = old_user
        if old_password is None:
            os.environ.pop("ADMIN_DEFAULT_PASSWORD", None)
        else:
            os.environ["ADMIN_DEFAULT_PASSWORD"] = old_password
        shutil.rmtree(self.root, ignore_errors=True)

    def test_reset_updates_account_clears_security_state_and_creates_backup(self) -> None:
        backup = reset_admin_credentials("安歌", "new-password...")
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertFalse(verify_admin_login("admin", "old-password"))
        self.assertTrue(verify_admin_login("安歌", "new-password..."))
        with db_connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM admin_login_attempts").fetchone()[0], 0)
        original_db = C.DB_FILE
        C.DB_FILE = backup
        try:
            self.assertTrue(verify_admin_login("admin", "old-password"))
        finally:
            C.DB_FILE = original_db

    def test_reset_rejects_short_password_without_mutating_database(self) -> None:
        with self.assertRaises(ValueError):
            reset_admin_credentials("安歌", "short")
        self.assertTrue(verify_admin_login("admin", "old-password"))
        self.assertFalse((C.DB_FILE.parent / "backups").exists())


if __name__ == "__main__":
    unittest.main()
