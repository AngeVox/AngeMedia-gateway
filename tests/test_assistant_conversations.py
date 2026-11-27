"""会话迁移、账户隔离、旧入口旁路和归档失租的可观察合同。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import angemedia_gateway.config as C
from angemedia_gateway.assistant_runtime_contract import RunLeaseLost, RunNotFound
from angemedia_gateway.db.connection import db_connect, db_transaction
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.admin_auth import update_admin_account
from angemedia_gateway.repositories.assistant_conversations import AssistantConversationRepository, ConversationNotFound
from angemedia_gateway.repositories.assistant_runtime import AssistantRunRepository
from angemedia_gateway.repositories.assistant_sessions import (
    add_assistant_message, add_assistant_run, create_assistant_session, delete_assistant_session,
    get_assistant_session, list_assistant_messages, list_assistant_sessions, touch_assistant_session,
)
from angemedia_gateway.security import hash_password


class ConversationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password("fixture-password")

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="assistant-conversation-"))
        self.original_db = C.DB_FILE
        C.DB_FILE = self.temp / "state.db"
        init_db()
        with closing(db_connect()) as conn:
            for name in ("alice", "bob"):
                conn.execute("INSERT INTO admin_users VALUES(?,?,?,?)", (name, self.password_hash, "old", "old"))
        self.repo = AssistantConversationRepository()
        self.runs = AssistantRunRepository()

    def tearDown(self):
        C.DB_FILE = self.original_db
        assert self.temp.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()) and self.temp.name.startswith("assistant-conversation-")
        shutil.rmtree(self.temp)

    def create(self):
        return self.repo.create(username="alice", title="山间云雾")['id']

    def test_accounts_cannot_read_list_or_archive_each_others_conversations(self):
        session = self.create()
        self.assertEqual(self.repo.list(username="alice")["total"], 1)
        self.assertEqual(self.repo.list(username="bob")["items"], [])
        for identity in ("bob", "missing"):
            for operation in (lambda: self.repo.detail(session, username=identity),
                              lambda: self.repo.archive(session, username=identity)):
                with self.subTest(identity=identity), self.assertRaises(ConversationNotFound):
                    operation()
        self.assertEqual(self.repo.detail(session, username="alice")["session"]["title"], "山间云雾")

    def test_legacy_helpers_never_read_write_delete_or_touch_account_sessions(self):
        session = self.create()
        run = self.runs.admit(session, uuid4().hex, {"message": "保持蓝色"}).run
        lease = self.runs.claim(run["id"])
        self.runs.publish_message(lease, "user", "保持蓝色")
        self.assertIsNone(get_assistant_session(session))
        self.assertEqual(list_assistant_sessions()["total"], 0)
        self.assertEqual(list_assistant_messages(session), [])
        self.assertFalse(delete_assistant_session(session))
        for operation in (lambda: get_assistant_session(session, reject_private=True),
                          lambda: add_assistant_message("late", session, "assistant", "共享消息"),
                          lambda: add_assistant_run("late", session, "succeeded", None, {}, {}, [])):
            with self.assertRaises(ValueError):
                operation()
        touch_assistant_session(session, title="不应改变")
        self.assertEqual(self.repo.detail(session, username="alice")["session"]["title"], "山间云雾")
        self.runs.finish(lease, answer="完成")
        self.assertEqual([m["content"] for m in self.repo.detail(session, username="alice")["messages"]], ["保持蓝色", "完成"])

    def test_archive_revokes_current_lease_and_is_idempotent_for_owner(self):
        session = self.create()
        run_id = self.runs.admit(session, uuid4().hex, {"message": "不要复活"}).run["id"]
        lease = self.runs.claim(run_id)
        self.runs.publish_message(lease, "user", "已开始")
        self.repo.archive(session, username="alice")
        self.repo.archive(session, username="alice")
        for operation in (lambda: self.runs.get(run_id), lambda: self.runs.finish(lease, answer="迟到"),
                          lambda: self.runs.publish_message(lease, "assistant", "迟到")):
            with self.assertRaises((RunNotFound, RunLeaseLost)):
                operation()
        with self.assertRaises(ConversationNotFound):
            self.repo.detail(session, username="alice")
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM assistant_session_owners WHERE session_id=?", (session,)).fetchone()[0], 1)
            for table in ("assistant_messages", "assistant_runs", "assistant_session_leases"):
                self.assertEqual(conn.execute(f"SELECT count(*) FROM {table} WHERE session_id=?", (session,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_account_rename_preserves_ownership_and_cannot_be_taken_by_reusing_name(self):
        session = self.create()
        update_admin_account("alice", current_password="fixture-password", new_username="安歌测试")
        with closing(db_connect()) as conn:
            conn.execute("INSERT INTO admin_users VALUES(?,?,?,?)", ("alice", self.password_hash, "new", "new"))
        self.assertEqual(self.repo.list(username="alice")["total"], 0)
        self.assertEqual(self.repo.detail(session, username="安歌测试")["session"]["id"], session)
        with self.assertRaises(ConversationNotFound):
            self.repo.archive(session, username="alice")

    def test_database_rejects_delete_of_account_with_owned_sessions(self):
        session = self.create()
        self.repo.archive(session, username="alice")
        with closing(db_connect()) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM admin_users WHERE username='alice'")

    def test_package_admin_reset_preserves_ownership_and_backups(self):
        from angemedia_gateway.cli.reset_admin import reset_admin_credentials
        session = self.create()
        with closing(db_connect()) as conn:
            conn.execute("DELETE FROM admin_users WHERE username='bob'")
        backup = reset_admin_credentials("recovered-admin", "recovered-fixture-password")
        self.assertTrue(backup.is_file())
        self.assertTrue(backup.resolve().is_relative_to(self.temp.resolve()))
        self.assertEqual(self.repo.detail(session, username="recovered-admin")["session"]["id"], session)
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute("SELECT username FROM assistant_session_owners").fetchone()[0], "alice")

    def test_scope_cannot_downgrade_and_missing_owner_fails_closed(self):
        session = self.create()
        with closing(db_connect()) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE assistant_sessions SET access_scope='shared' WHERE id=?", (session,))
            conn.execute("DELETE FROM assistant_session_owners WHERE session_id=?", (session,))
        self.assertIsNone(get_assistant_session(session))
        self.assertFalse(delete_assistant_session(session))
        self.assertEqual(self.repo.list(username="alice")["total"], 0)
        with self.assertRaises(ConversationNotFound):
            self.repo.detail(session, username="alice")

    def test_legacy_sessions_cannot_be_claimed_by_new_accounts(self):
        create_assistant_session("shared", "历史资料")
        with closing(db_connect()) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO assistant_session_owners VALUES('shared','alice')")
        self.assertEqual(self.repo.list(username="alice")["total"], 0)
        self.assertIsNotNone(get_assistant_session("shared"))

    def test_details_bound_recent_history_and_only_project_safe_fields(self):
        session = self.create()
        run = self.runs.admit(session, uuid4().hex, {"message": "历史"}).run
        lease = self.runs.claim(run["id"])
        self.runs.publish_message(lease, "user", "第一条")
        self.runs.finish(lease, answer="第二条")
        with closing(db_connect()) as conn:
            conn.execute("UPDATE assistant_messages SET safe_payload_json=? WHERE session_id=?", ('{"owner_token":"private-owner"}', session))
        detail = self.repo.detail(session, username="alice", message_limit=1)
        self.assertEqual(detail["message_total"], 2)
        self.assertEqual(detail["messages"][0]["content"], "第二条")
        self.assertNotIn("private-owner", str(detail))
        self.assertNotIn("owner_token", str(detail))
        self.assertEqual(set(detail["messages"][0]), {"id", "session_id", "role", "content", "created_at"})

    def test_frozen_database_path_prevents_cross_database_access(self):
        session = self.create()
        C.DB_FILE = self.temp / "other.db"
        init_db()
        self.assertEqual(self.repo.detail(session, username="alice")["session"]["id"], session)
        self.assertEqual(AssistantConversationRepository().list(username="alice")["total"], 0)

    def test_invalid_parameters_and_stale_principal_never_create_rows(self):
        for title in ("", "   ", "a" * 121, 123):
            with self.subTest(title=title), self.assertRaises(ValueError):
                self.repo.create(username="alice", title=title)
        with self.assertRaises(ConversationNotFound):
            self.repo.create(username="missing", title="不可创建")
        self.assertEqual(self.repo.list(username="alice")["total"], 0)
        for limit in (0, 101, True):
            with self.assertRaises(ValueError):
                self.repo.list(username="alice", limit=limit)

    def test_archive_waiting_on_writer_rechecks_ownership_inside_write_transaction(self):
        session = self.create()
        entered = threading.Event()
        def archive():
            entered.set()
            self.repo.archive(session, username="alice")
        with ThreadPoolExecutor(max_workers=1) as pool:
            with db_transaction(immediate=True) as conn:
                future = pool.submit(archive)
                self.assertTrue(entered.wait(2))
                conn.execute("UPDATE admin_users SET username='renamed' WHERE username='alice'")
            with self.assertRaises(ConversationNotFound):
                future.result(timeout=5)
        self.assertEqual(self.repo.detail(session, username="renamed")["session"]["status"], "active")

    def test_partial_archive_failure_rolls_back_messages_run_lease_and_status(self):
        session = self.create()
        run_id = self.runs.admit(session, uuid4().hex, {"message": "事务"}).run["id"]
        lease = self.runs.claim(run_id)
        self.runs.publish_message(lease, "user", "原消息")
        with closing(db_connect()) as conn:
            conn.execute("CREATE TRIGGER block_run_delete BEFORE DELETE ON assistant_runs BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.archive(session, username="alice")
        self.assertEqual(self.repo.detail(session, username="alice")["messages"][0]["content"], "原消息")
        self.runs.renew(lease)
        self.assertEqual(self.runs.get(run_id)["status"], "running")

    def test_migration_failure_rolls_back_column_table_and_legacy_records(self):
        import angemedia_gateway.db.migrations as migrations
        original = dict(migrations.MIGRATIONS)["assistant_access_v1"]
        path = self.temp / "legacy.db"
        C.DB_FILE = path
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript("CREATE TABLE assistant_sessions(id TEXT PRIMARY KEY,title TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL); "
                               "INSERT INTO assistant_sessions VALUES('shared','原标题','active','old','old');")
        def fail(conn):
            original(conn)
            raise RuntimeError("故障注入")
        injected = tuple((version, fail if version == "assistant_access_v1" else fn) for version, fn in migrations.MIGRATIONS)
        with patch.object(migrations, "MIGRATIONS", injected), self.assertRaises(RuntimeError):
            init_db()
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute("SELECT * FROM assistant_sessions").fetchall(), [("shared", "原标题", "active", "old", "old")])
            self.assertNotIn("assistant_session_owners", [r[0] for r in conn.execute("SELECT name FROM sqlite_master")])
        init_db()
        init_db()
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute("SELECT access_scope FROM assistant_sessions").fetchone()[0], "shared")
            self.assertEqual(conn.execute("SELECT count(*) FROM assistant_session_owners").fetchone()[0], 0)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_missing_migration_marker_never_overwrites_existing_ownership(self):
        session = self.create()
        with closing(db_connect()) as conn:
            conn.execute("DELETE FROM schema_migrations WHERE version='assistant_access_v1'")
        with self.assertRaises(RuntimeError):
            init_db()
        self.assertEqual(self.repo.detail(session, username="alice")["session"]["id"], session)


if __name__ == "__main__":
    unittest.main()
