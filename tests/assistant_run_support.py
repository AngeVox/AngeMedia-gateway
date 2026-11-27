"""运行服务测试的独立数据库与真实登录Cookie夹具，不连接供应商。"""
from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

import angemedia_gateway.config as C
from angemedia_gateway.assistant_run_access import RunAccess
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.admin_auth import create_admin_session
from angemedia_gateway.repositories.assistant_conversations import AssistantConversationRepository
from angemedia_gateway.repositories.assistant_runtime import AssistantRunRepository
from angemedia_gateway.security import admin_session_context, hash_token


class RunFixture:
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="assistant-run-service-"))
        self.original_db = C.DB_FILE
        C.DB_FILE = self.temp / "state.db"
        init_db()
        with closing(db_connect()) as conn:
            for name in ("alice", "bob"):
                conn.execute("INSERT INTO admin_users VALUES(?,?,?,?)", (name, "unused-fixture-hash", "old", "old"))
        self.cookies, self.access = {}, {}
        for name in ("alice", "bob"):
            token, _ = create_admin_session(name)
            self.cookies[name] = token
            self.access[name] = RunAccess(name, hash_token(token))
        self.session = AssistantConversationRepository().create(username="alice", title="运行合同")["id"]
        self.repo = AssistantRunRepository(account_only=True)

    def studio_credentials(self, username):
        token = self.cookies[username]
        return {
            "cookies": {"am_admin_session": token},
            "headers": {"X-Studio-Session-Context": admin_session_context(token)},
        }

    def tearDown(self):
        C.DB_FILE = self.original_db
        assert self.temp.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()) and self.temp.name.startswith("assistant-run-service-")
        shutil.rmtree(self.temp)
