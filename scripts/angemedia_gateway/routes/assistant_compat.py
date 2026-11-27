"""保留旧客户端使用的助手规划、会话和对话接口。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from .. import config as C
from ..repositories.assistant_sessions import (
    delete_assistant_session,
    get_assistant_session,
    list_assistant_messages,
    list_assistant_sessions,
)
from ..runtime import require_admin_auth, require_auth
from ..schemas import AssistantRequest, EnhanceRequest
from ..services.assistant_business_policy import business_readiness
from ..services.assistant_chat_adapter import ChatRequest, admit_chat, stream_chat, wait_chat
from ..services.assistant_chat_service import build_assistant_chat_reply
from ..services.assistant_event_stream import sse_frame
from ..services.assistant_planner import build_assistant_recommendation
from ..services.prompt_copilot import build_prompt_copilot
from .admin_access import AdminSessionAccess
from .assistant_run_http import require_run_service, run_result

log = logging.getLogger("angemedia-gateway")
router = APIRouter()

_ASSISTANT_INVALID_REQUEST_DETAIL = {
    "message": "invalid assistant request",
    "code": "invalid_assistant_request",
}
_ASSISTANT_SERVICE_UNAVAILABLE_DETAIL = {
    "message": "assistant service unavailable",
    "code": "assistant_service_unavailable",
}


@router.post("/v1/assistant/prompt-copilot")
async def assistant_prompt_copilot(
    req: EnhanceRequest,
    session: dict[str, Any] = Depends(require_admin_auth),
) -> dict[str, Any]:
    if session.get("auth_type") != "session":
        raise HTTPException(status_code=403, detail="gateway API keys cannot access Prompt Copilot")
    try:
        return await build_prompt_copilot(req)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=_ASSISTANT_INVALID_REQUEST_DETAIL,
        ) from None
    except Exception:
        log.exception("Prompt Copilot failed")
        raise HTTPException(
            status_code=502,
            detail=_ASSISTANT_SERVICE_UNAVAILABLE_DETAIL,
        ) from None


@router.post("/v1/assistant/plan", dependencies=[Depends(require_auth)])
async def assistant_plan(req: AssistantRequest) -> dict[str, Any]:
    return await build_assistant_recommendation(req)


@router.get("/v1/admin/assistant/sessions")
async def assistant_sessions(
    limit: int = 20,
    offset: int = 0,
    session: dict[str, Any] = Depends(require_admin_auth),
) -> dict[str, Any]:
    if session.get("auth_type") != "session":
        raise HTTPException(status_code=403, detail="gateway API keys cannot access assistant sessions")
    return list_assistant_sessions(limit=limit, offset=offset)


@router.get("/v1/admin/assistant/sessions/{session_id}")
async def assistant_session_detail(
    session_id: str,
    session: dict[str, Any] = Depends(require_admin_auth),
) -> dict[str, Any]:
    if session.get("auth_type") != "session":
        raise HTTPException(status_code=403, detail="gateway API keys cannot access assistant sessions")
    item = get_assistant_session(session_id)
    if not item:
        raise HTTPException(status_code=404, detail="assistant session not found")
    return {"session": item, "messages": list_assistant_messages(session_id, limit=100, recent=True)}


@router.delete("/v1/admin/assistant/sessions/{session_id}")
async def delete_assistant_session_route(
    session_id: str,
    session: dict[str, Any] = Depends(require_admin_auth),
) -> dict[str, Any]:
    if session.get("auth_type") != "session":
        raise HTTPException(status_code=403, detail="gateway API keys cannot delete assistant sessions")
    if not delete_assistant_session(session_id):
        raise HTTPException(status_code=404, detail="assistant session not found")
    return {"deleted": True, "session_id": session_id}


@router.post("/v1/assistant/chat")
async def assistant_chat(
    payload: dict[str, Any],
    request: Request, response: Response, access: AdminSessionAccess,
) -> dict[str, Any]:
    response.headers['Cache-Control'] = 'no-store'
    initial, service = await _chat_admission(request, access, payload)
    if service is not None:
        return await run_result(wait_chat(service, access, initial))
    return initial


async def _chat_admission(request, access, payload):
    try:
        ChatRequest.model_validate(payload)
    except ValueError:
        raise HTTPException(status_code=400, detail=_ASSISTANT_INVALID_REQUEST_DETAIL) from None
    if await asyncio.to_thread(business_readiness, C.DB_FILE) != 'running':
        # 只为旧客户端保留显式本地帮助；配置变化也绝不掉回旧模型循环。
        try:
            return await build_assistant_chat_reply(payload), None
        except ValueError:
            raise HTTPException(status_code=400, detail=_ASSISTANT_INVALID_REQUEST_DETAIL) from None
        except Exception:
            raise HTTPException(status_code=502, detail=_ASSISTANT_SERVICE_UNAVAILABLE_DETAIL) from None
    service = require_run_service(request)
    if payload.get('session_id') and await asyncio.to_thread(get_assistant_session, payload['session_id']):
        raise HTTPException(status_code=409, detail={'code': 'legacy_session_read_only'})
    return await run_result(admit_chat(service, access, payload)), service


@router.post("/v1/assistant/chat/stream")
async def assistant_chat_stream(
    payload: dict[str, Any],
    request: Request, access: AdminSessionAccess,
    last_event_id: str | None = Header(None, alias='Last-Event-ID'),
) -> StreamingResponse:
    if last_event_id is not None:
        raise HTTPException(status_code=400, detail={'code': 'resume_with_run_get_stream'})
    initial, service = await _chat_admission(request, access, payload)
    async def local_stream():
        yield sse_frame('status', {'stage': 'local_help', 'session_id': initial['session_id']})
        yield sse_frame('timeline', {'items': initial['timeline']})
        yield sse_frame('chunk', {'content': initial['answer']})
        yield sse_frame('done', {'session_id': initial['session_id'], 'status': initial['status'],
                                'run_id': initial['run']['id'], 'message_id': initial['message']['id']})
    return StreamingResponse(
        stream_chat(service, access, initial) if service is not None else local_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", 'X-Accel-Buffering': 'no'},
    )
