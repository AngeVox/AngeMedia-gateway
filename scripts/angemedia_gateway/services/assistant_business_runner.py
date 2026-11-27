"""真实业务引擎装配；SDK与类型化工具延迟在线程中加载，运行间不共享主体。"""
from __future__ import annotations

import asyncio

from ..assistant_async import settle_on_cancel
from ..assistant_execution import RunResult
from ..db.readonly_scope import readonly_database
from .assistant_business_policy import AssistantPolicyDenied, authorize_business
from .assistant_config_service import resolve_assistant_runtime
from .assistant_business_secrets import configured_secrets


class BusinessAssistantRunner:
    def __init__(self, checkpoint_db):
        self.checkpoint_db = checkpoint_db

    def _engine(self, context):
        from .assistant_business_tools import business_tools
        from .assistant_action_tools import action_tools
        from .assistant_engine import AssistantEngine

        def config():
            with readonly_database(context.repository.db_path):
                return resolve_assistant_runtime()

        with readonly_database(context.repository.db_path):
            secrets = configured_secrets()
        return AssistantEngine(self.checkpoint_db, (*business_tools(), *action_tools()), config_resolver=config,
                               authorizer=authorize_business, context_schema=type(context), payload_secrets=secrets)

    async def __call__(self, context):
        try:
            await authorize_business(context)
        except AssistantPolicyDenied as exc:
            return RunResult("refused", {"error_code": str(exc)})
        engine = await settle_on_cancel(asyncio.to_thread(self._engine, context))
        return await engine(context)
