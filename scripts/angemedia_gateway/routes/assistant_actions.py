"""人类动作查询、修订、撤回与确认；模型只能提案，不能调用此确认入口。"""
from __future__ import annotations

import asyncio
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from ..assistant_action_contract import ActionConfirmation, ActionError
from ..assistant_run_access import RunAuthenticationExpired
from ..repositories.assistant_conversations import ConversationNotFound
from ..services.assistant_action_csrf import action_csrf_token, require_action_csrf
from ..services.assistant_action_service import AssistantActionService
from ..services.job_control import JobControlError
from .admin_access import AdminSessionAccess


class SafeActionRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, detail={'code': 'invalid_action_request'}) from None
        return safe


router = APIRouter(prefix='/v1/admin/assistant', route_class=SafeActionRoute)
ActionId = Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]


class ProposalRequestKey(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    request_key: str = Field(min_length=32, max_length=36)

    @field_validator('request_key')
    @classmethod
    def uuid_key(cls, value):
        return UUID(value).hex


class Revision(ProposalRequestKey):
    version: int = Field(ge=1)
    changes: dict[str, JsonValue] = Field(min_length=1, max_length=48)


class Regeneration(ProposalRequestKey):
    job_version: int = Field(ge=0, le=9223372036854775807)


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: int = Field(ge=1)
    binding_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    acknowledgements: list[Literal['may_incur_cost', 'unverified_model', 'mutable_remote_reference']] = Field(max_length=3)


class JobCancellation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    job_version: int = Field(ge=0, le=9223372036854775807)


def action_service(request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    runtime = getattr(request.app.state, 'assistant_run_service', None)
    return AssistantActionService(db_path=runtime.repository.db_path if runtime is not None else None)


Service = Annotated[AssistantActionService, Depends(action_service)]


async def _call(function, *args, **kwargs):
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except RunAuthenticationExpired:
        raise HTTPException(401, detail={'code': 'authentication_expired'}) from None
    except ConversationNotFound:
        raise HTTPException(404, detail={'code': 'action_not_found'}) from None
    except (ActionError, JobControlError) as error:
        code = error.code
        status = 404 if code == 'action_not_found' else 422 if code.startswith('invalid_') else 429 if code in {'action_capacity_reached', 'action_revision_limit'} else 409
        raise HTTPException(status, detail={'code': code}) from None


async def _csrf(request, service, access):
    expected = await _call(action_csrf_token, service.db_path, access)
    require_action_csrf(request, expected)


@router.get('/conversations/{session_id}/actions')
async def list_actions(session_id: ActionId, access: AdminSessionAccess, service: Service,
                       limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=10000)):
    result = await _call(service.list, session_id, access=access, limit=limit, offset=offset)
    result['csrf_token'] = await _call(action_csrf_token, service.db_path, access)
    return result


@router.get('/actions/{action_id}')
async def inspect_action(action_id: ActionId, access: AdminSessionAccess, service: Service):
    return {'action': await _call(service.inspect, action_id, access=access),
            'csrf_token': await _call(action_csrf_token, service.db_path, access)}


@router.get('/actions/{action_id}/job-control')
async def job_control(action_id: ActionId, access: AdminSessionAccess, service: Service):
    return {**await _call(service.job_control, action_id, access=access),
            'csrf_token': await _call(action_csrf_token, service.db_path, access)}


@router.post('/actions/{action_id}/cancel-job')
async def cancel_job(action_id: ActionId, payload: JobCancellation, request: Request, access: AdminSessionAccess, service: Service):
    await _csrf(request, service, access)
    return await _call(service.cancel_job, action_id, payload.job_version, access=access)


@router.post('/actions/{action_id}/regenerate')
async def regenerate_action(action_id: ActionId, payload: Regeneration, request: Request, access: AdminSessionAccess, service: Service):
    await _csrf(request, service, access)
    return await _call(service.regenerate, action_id, payload.job_version, payload.request_key, access=access)


@router.post('/actions/{action_id}/revisions')
async def revise_action(action_id: ActionId, payload: Revision, request: Request, access: AdminSessionAccess, service: Service):
    await _csrf(request, service, access)
    return await _call(service.revise_patch, action_id, payload.version, payload.request_key, payload.changes, access=access)


@router.post('/actions/{action_id}/revoke')
async def revoke_action(action_id: ActionId, request: Request, access: AdminSessionAccess, service: Service):
    await _csrf(request, service, access)
    return {'action': await _call(service.revoke, action_id, access=access)}


@router.post('/actions/{action_id}/confirm')
async def confirm_action(action_id: ActionId, payload: Confirmation, request: Request, access: AdminSessionAccess, service: Service, response: Response):
    await _csrf(request, service, access)
    confirmation = ActionConfirmation(payload.version, payload.binding_digest, frozenset(payload.acknowledgements))
    result = await _call(service.confirm, action_id, confirmation, access=access)
    response.status_code = 202 if result['created'] else 200
    return result
