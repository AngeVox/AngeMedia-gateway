"""应用生命周期持有真实业务助手；启停与配置在提交和执行时动态复核。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from ..repositories.assistant_runtime import AssistantRunRepository
from .assistant_run_service import AssistantRunService
from .assistant_business_policy import business_readiness, require_admission
from .assistant_business_runner import BusinessAssistantRunner


def build_assistant_run_service() -> AssistantRunService:
    repository = AssistantRunRepository(account_only=True)
    checkpoint_db = repository.db_path.with_name(repository.db_path.stem + ".assistant-checkpoints.sqlite3")
    return AssistantRunService(repository, checkpoint_db=checkpoint_db, runner=BusinessAssistantRunner(checkpoint_db),
                               readiness=business_readiness, admission_check=require_admission)


@asynccontextmanager
async def assistant_lifespan(app):
    factory = getattr(app.state, "assistant_service_factory", build_assistant_run_service)
    service = factory()
    app.state.assistant_run_service = service
    try:
        await service.start()
        yield
    finally:
        try:
            await service.aclose()
        finally:
            del app.state.assistant_run_service
