"""动作域共用真实准备、登录、入队数据库夹具；不调用外部供应商。"""
from __future__ import annotations

from contextlib import closing
from unittest.mock import patch
from uuid import uuid4

from assistant_run_support import RunFixture
import angemedia_gateway.config as C
from angemedia_gateway.assistant_action_contract import ActionConfirmation, ActionError
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.settings import set_config_many
from angemedia_gateway.services.assistant_action_service import AssistantActionService


class ActionFixture(RunFixture):
    def setUp(self):
        super().setUp()
        self.output = self.temp / 'generated'
        self.uploads = self.temp / 'uploads'
        self.output.mkdir(); self.uploads.mkdir()
        self.config_patch = patch.multiple(C, OUTPUT_DIR=self.output, UPLOAD_DIR=self.uploads,
            SILICONFLOW_API_KEY='fixture-siliconflow-credential-123456789',
            AGNES_API_KEY='fixture-agnes-credential-123456789')
        self.config_patch.start()
        self.smoke_patch = patch.dict('os.environ', {'QUEUE_SMOKE_FAKE_PROVIDERS': 'false'})
        self.smoke_patch.start()
        set_config_many({'ANGE_ASSISTANT_ENABLED': 'true', 'ANGE_ASSISTANT_ALLOW_PAID': 'true',
                         'ANGE_ASSISTANT_ALLOW_AGNES': 'true'})
        self.now = 1800000000000
        self.service = AssistantActionService(clock=lambda: self.now, ttl_ms=1000)
        self.alice = self.access['alice']; self.bob = self.access['bob']

    def tearDown(self):
        self.smoke_patch.stop(); self.config_patch.stop()
        super().tearDown()

    def propose(self, *, service=None, request=None, kind='image', key=None):
        return (service or self.service).propose(self.session, key or uuid4().hex, kind,
            request if request is not None else {'prompt': '蓝色山谷', 'model': 'kolors'}, access=self.alice)['action']

    def confirmation(self, action, *, acknowledgements=None):
        return ActionConfirmation(action['version'], action['binding_digest'],
            frozenset(action['review']['required_acknowledgements'] if acknowledgements is None else acknowledgements))

    def confirm(self, action, *, service=None, access=None):
        return (service or self.service).confirm(action['id'], self.confirmation(action), access=access or self.alice)

    def counts(self):
        with closing(db_connect()) as conn:
            return tuple(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                for table in ('jobs', 'job_dispatches', 'job_submission_keys'))

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(ActionError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
