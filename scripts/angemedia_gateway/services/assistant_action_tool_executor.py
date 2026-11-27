"""模型只写提案；稳定原生调用身份、当前租约及完整参数由服务端负责。"""
from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid5

from ..assistant_async import settle_on_cancel
from ..assistant_action_access import ProposalRunAccess
from ..assistant_action_contract import ActionError, PROPOSAL_TOOL_ERRORS
from ..assistant_runtime_contract import RunLeaseLost
from .assistant_action_service import AssistantActionService
from .assistant_action_specs import ACTION_TOOLS
from .assistant_business_policy import authorize_business, AssistantPolicyDenied

_SPECS = {spec.name: spec for spec in ACTION_TOOLS}


def proposal_summary(action):
    review = action['review'] or {}
    request = review.get('request', {})
    return {'id': action['id'], 'version': action['version'], 'current_version': action['current_version'],
            'status': action['status'], 'kind': review.get('kind'), 'allowed': review.get('allowed', False),
            'blocked_reasons': review.get('blocked_reasons', []), 'prompt_excerpt': request.get('prompt', '')[:240],
            'prompt_is_excerpt': len(request.get('prompt', '')) > 240,
            'parameters': {key: request[key][:160] if isinstance(request[key], str) else request[key]
                           for key in ('model', 'provider_model', 'size', 'seconds', 'aspect_ratio', 'n',
                                       'operation', 'complexity', 'output_format', 'frame_count', 'frame_duration_ms', 'max_images', 'text_recognition') if key in request},
            'has_markup': bool(request.get('markup')),
            'routes': [{'provider': item['provider'], 'model': item['model'][:160]} for item in review.get('routes', [])[:2]],
            'route_count': len(review.get('routes', [])), 'metadata_is_summary': True,
            'cost': review.get('cost'), 'required_acknowledgements': review.get('required_acknowledgements', []),
            'expires_ms': action['expires_ms'], 'job': action['job']}


def _apply(context, name, arguments, call_id):
    context.raise_if_stopped()
    service = AssistantActionService(db_path=context.repository.db_path)
    access = ProposalRunAccess(context.repository, context.lease)
    key = uuid5(UUID(context.lease.run_id), name + ':' + call_id).hex
    if name == 'media_action_list':
        page = service.list(context.lease.session_id, access=access, limit=arguments['limit'])
        data = {'items': [proposal_summary(action) for action in page['items']], 'total': page['total']}
        while len(json.dumps(data, ensure_ascii=False)) > 6000 and len(data['items']) > 1:
            data['items'].pop()
        return data
    if name == 'revise_media_proposal':
        result = service.revise_patch(arguments['action_id'], arguments['version'], key, arguments['changes'], access=access)
    else:
        kind = 'image' if name == 'propose_image' else 'video'
        # 只导出实际设置项，避免把视频2.5禁止的旧版默认字段重新填入请求。
        request = arguments.request.model_dump(exclude_unset=True)
        result = service.propose(context.lease.session_id, key, kind, request, access=access)
    return {'created': result['created'], 'action': proposal_summary(result['action']), 'requires_human_confirmation': result['action']['status'] != 'submitted'}


async def execute_action_tool(context, name, arguments, call_id):
    await authorize_business(context)
    spec = _SPECS.get(name)
    if spec is None or not isinstance(call_id, str) or not 1 <= len(call_id) <= 128:
        return {'tool': name, 'status': 'error', 'code': 'unknown_tool'}
    try:
        parsed = spec.schema.model_validate(arguments, strict=True)
    except ValueError:
        return {'tool': name, 'status': 'error', 'code': 'invalid_tool_arguments'}
    # 生成请求保留Pydantic字段来源；列表/修订只需要普通字典。
    values = parsed if name in {'propose_image', 'propose_video'} else parsed.model_dump()
    try:
        data = await settle_on_cancel(asyncio.to_thread(_apply, context, name, values, call_id))
        result = {'tool': name, 'status': 'done', 'data': data}
    except ActionError as error:
        result = {'tool': name, 'status': 'error', 'code': error.code if error.code in PROPOSAL_TOOL_ERRORS else 'proposal_rejected'}
    except (RunLeaseLost, AssistantPolicyDenied):
        raise
    except Exception:
        result = {'tool': name, 'status': 'error', 'code': 'proposal_rejected'}
    await authorize_business(context)
    return result
