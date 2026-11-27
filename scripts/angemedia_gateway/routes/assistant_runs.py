"""运行提交、JSON观察、显式取消与SSE订阅；执行由应用生命周期持有。"""
from __future__ import annotations

from typing import Annotated, Literal
import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..assistant_runtime_contract import TERMINAL_RUN_STATUSES
from ..services.assistant_event_stream import event_cursor, run_event_stream
from ..services.assistant_run_service import AssistantRunService
from .admin_access import AdminSessionAccess
from .assistant_run_http import require_run_service, run_result

router = APIRouter(prefix="/v1/admin/assistant")


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_key: str = Field(min_length=32, max_length=36)
    message: str = Field(min_length=1, max_length=8000)
    language: Literal['zh', 'en'] | None = None

    @field_validator("request_key")
    @classmethod
    def valid_request_key(cls, value: str) -> str:
        return UUID(value).hex

    @field_validator("message")
    @classmethod
    def nonblank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("消息不能为空")
        return value


def run_service(request: Request, response: Response) -> AssistantRunService:
    response.headers["Cache-Control"] = "no-store"
    return require_run_service(request)


Service = Annotated[AssistantRunService, Depends(run_service)]


@router.get("/runtime")
async def runtime_status(access: AdminSessionAccess, service: Service):
    return await service.availability()


@router.post("/conversations/{session_id}/runs")
async def submit_run(session_id: str, payload: RunCreate, access: AdminSessionAccess, service: Service, response: Response):
    result = await run_result(service.submit(session_id, payload.request_key, payload.message, access=access, language=payload.language))
    response.status_code = 202 if result["created"] else 200
    return result


@router.get("/runs/{run_id}")
async def inspect_run(run_id: str, access: AdminSessionAccess, service: Service):
    snapshot = await run_result(service.inspect(run_id, access=access, limit=1))
    return {"run": snapshot["run"]}


@router.get('/conversations/{session_id}/activity')
async def conversation_activity(session_id: str, access: AdminSessionAccess, service: Service):
    run_id = await run_result(asyncio.to_thread(service.repository.activity, session_id, access=access))
    if run_id is None:
        return {'run': None}
    return await inspect_run(run_id, access, service)


@router.get('/knowledge')
async def local_knowledge(access: AdminSessionAccess, query: str = Query(min_length=1, max_length=500)):
    # 显式本地资料查询不创建共享会话，也不冒充模型或工具运行。
    from ..services.assistant_knowledge_base import search_assistant_knowledge
    return {'items': await asyncio.to_thread(search_assistant_knowledge, query, limit=4), 'mode': 'local_knowledge'}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, access: AdminSessionAccess, service: Service):
    snapshot = await run_result(service.cancel(run_id, access=access))
    return {"run": snapshot["run"]}


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, access: AdminSessionAccess, service: Service,
                     after: int = Query(0, ge=0, le=9223372036854775807), limit: int = Query(100, ge=1, le=500)):
    return await run_result(service.inspect(run_id, access=access, after=after, limit=limit))


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, access: AdminSessionAccess, service: Service,
                     after: int | None = Query(None, ge=0, le=9223372036854775807),
                     last_event_id: str | None = Header(None, alias="Last-Event-ID")):
    try:
        cursor = event_cursor(run_id, after, last_event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "invalid_event_cursor"}) from None
    initial = await run_result(service.inspect(run_id, access=access, after=cursor, limit=50))
    if initial["run"]["status"] in TERMINAL_RUN_STATUSES and cursor == initial["run"]["event_seq"]:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return StreamingResponse(run_event_stream(service, run_id, access, initial), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
