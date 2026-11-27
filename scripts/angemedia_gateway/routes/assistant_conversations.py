"""账户会话HTTP合同；SQLite处理交由同步路由在线程池执行。"""
from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..repositories.assistant_conversations import AssistantConversationRepository, ConversationNotFound
from ..runtime import require_admin_auth
from ..assistant_runtime_contract import RunRequestConflict

router = APIRouter(prefix="/v1/admin/assistant/conversations")
Principal = Annotated[dict[str, Any], Depends(require_admin_auth)]


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=120)
    request_key: str | None = Field(default=None, min_length=32, max_length=36)
    source_session_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator('request_key')
    @classmethod
    def valid_key(cls, value):
        return UUID(value).hex if value is not None else None

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, title: str) -> str:
        if not title.strip():
            raise ValueError("会话标题不能为空")
        return title.strip()


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="assistant conversation not found")


@router.post("", status_code=201)
def create_conversation(payload: ConversationCreate, principal: Principal, response: Response) -> dict[str, Any]:
    response.headers['Cache-Control'] = 'no-store'
    try:
        return {"session": AssistantConversationRepository().create(username=principal["username"], **payload.model_dump())}
    except RunRequestConflict:
        raise HTTPException(status_code=409, detail={'code': 'request_conflict'}) from None
    except ConversationNotFound:
        raise _not_found() from None


@router.get("")
def list_conversations(principal: Principal, response: Response, limit: int = Query(20, ge=1, le=100),
                       offset: int = Query(0, ge=0)) -> dict[str, Any]:
    response.headers['Cache-Control'] = 'no-store'
    return AssistantConversationRepository().list(username=principal["username"], limit=limit, offset=offset)


@router.get("/{session_id}")
def conversation_detail(session_id: str, principal: Principal, response: Response,
                        message_limit: int = Query(50, ge=1, le=100),
                        before: str | None = Query(None, min_length=1, max_length=64)) -> dict[str, Any]:
    response.headers['Cache-Control'] = 'no-store'
    try:
        return AssistantConversationRepository().detail(session_id, username=principal["username"], message_limit=message_limit, before=before)
    except ConversationNotFound:
        raise _not_found() from None


@router.delete("/{session_id}")
def archive_conversation(session_id: str, principal: Principal) -> dict[str, Any]:
    try:
        AssistantConversationRepository().archive(session_id, username=principal["username"])
        return {"deleted": True, "session_id": session_id}
    except ConversationNotFound:
        raise _not_found() from None
