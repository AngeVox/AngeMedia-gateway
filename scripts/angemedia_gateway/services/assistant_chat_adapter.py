"""旧聊天响应的薄适配器；不持有模型循环，不因订阅断开取消运行。"""
from __future__ import annotations

import asyncio
from typing import Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..assistant_runtime_contract import TERMINAL_RUN_STATUSES
from ..assistant_run_access import RunUnavailable
from ..repositories.assistant_conversations import AssistantConversationRepository
from .assistant_event_stream import sse_frame, watch_run


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = Field(default=None, min_length=1, max_length=64)
    request_key: str = Field(default_factory=lambda: uuid4().hex)
    language: Literal['zh', 'en'] | None = None

    @field_validator('request_key')
    @classmethod
    def valid_key(cls, value):
        return UUID(value).hex

    @field_validator('message')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('消息不能为空')
        return value


async def admit_chat(service, access, payload):
    request = ChatRequest.model_validate(payload)
    session_id = request.session_id
    if session_id is None:
        # 无会话的旧客户端也能用同一请求键恢复首次提交，不生成第二个会话。
        repository = AssistantConversationRepository(db_path=service.repository.db_path)
        session = await asyncio.to_thread(repository.create, username=access.username,
            title=request.message[:80], request_key=request.request_key)
        session_id = session['id']
    accepted = await service.submit(session_id, request.request_key, request.message, access=access, language=request.language)
    initial = await service.inspect(accepted['run']['id'], access=access, limit=50)
    return initial


def chat_reply(run):
    message = {'id': run['assistant_message_id'], 'session_id': run['session_id'],
               'role': 'assistant', 'content': run['answer'] or ''}
    return {'session_id': run['session_id'], 'answer': run['answer'] or '', 'status': run['status'],
            'message': message, 'run': run, 'timeline': [], 'messages': [message]}


async def complete_reply(service, access, run):
    detail = await asyncio.to_thread(AssistantConversationRepository(db_path=service.repository.db_path).detail,
                                    run['session_id'], username=access.username)
    reply = {**chat_reply(run), 'session': detail['session'], 'messages': detail['messages']}
    reply['user_message'] = next((m for m in detail['messages'] if m['id'] == run['user_message_id']), None)
    return reply


async def wait_chat(service, access, initial):
    async for snapshot in watch_run(service, initial['run']['id'], access, initial):
        if not snapshot['has_more'] and snapshot['run']['status'] in TERMINAL_RUN_STATUSES:
            return await complete_reply(service, access, snapshot['run'])
    raise RunUnavailable('运行服务已关闭')


async def stream_chat(service, access, initial):
    run_id = initial['run']['id']
    yield sse_frame('status', {'stage': 'accepted', 'session_id': initial['run']['session_id'], 'run_id': run_id})
    try:
        async for snapshot in watch_run(service, run_id, access, initial):
            for item in snapshot['events']:
                yield sse_frame('timeline', {'items': [{'type': item['event_type'], **item['payload']}],
                    'run_id': run_id, 'sequence': item['sequence']}, event_id=f"{run_id}:{item['sequence']}")
            run = snapshot['run']
            if not snapshot['has_more'] and run['status'] in TERMINAL_RUN_STATUSES:
                if run['answer']:
                    yield sse_frame('chunk', {'content': run['answer']})
                yield sse_frame('done', {**await complete_reply(service, access, run), 'run_id': run_id, 'message_id': run['assistant_message_id']})
                return
    except Exception:
        # 旧协议仍只返回固定错误；恢复必须订阅GET运行流，不能重开模型调用。
        yield sse_frame('error', {'code': 'run_stream_unavailable', 'run_id': run_id})
