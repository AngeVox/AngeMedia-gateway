"""生成提案的费用与能力说明；目录声明不能冒充账户实测或免费额度。"""
from __future__ import annotations

from dataclasses import asdict

from ..assistant import assistant_allow_agnes, assistant_allow_paid, assistant_enabled
from ..job_submission import submission_intent_hash
from ..providers.catalog.loader import load_provider_catalog
from ..providers.catalog.policy import policy_api_response
from ..providers.runtime_config import resolve_live_provider_runtime_config
from ..repositories.settings import builtin_provider_enabled, get_custom_provider
from ..repositories.reference_relay_config import get_live_reference_relay_config
from .assistant_action_connections import connection_binding, needs_reference_relay


def action_policy(arguments, references, *, catalog=None):
    catalog = catalog or load_provider_catalog()
    payload = arguments['payload']; route = payload.get('route', {})
    if arguments['kind'] == 'video':
        targets = [(arguments['provider'], arguments['model'])]
    elif route.get('mode') == 'custom':
        targets = [('custom:' + route['custom_provider_id'], route['custom_default_model'])]
    else:
        targets = [(item['provider'], item['model']) for item in route['routes']]
    blocked, acknowledgements, public, bindings = [], {'may_incur_cost'}, [], []
    if not assistant_enabled(): blocked.append('assistant_disabled')
    # 当前没有可信的逐账户零费用报价；促销、积分和未知政策也不能绕过费用开关。
    if not assistant_allow_paid(): blocked.append('paid_generation_disabled')
    for provider_id, model_id in targets:
        model = None
        if provider_id.startswith('custom:'):
            provider = get_custom_provider(provider_id.split(':', 1)[1], include_secret=True)
            enabled = bool(provider and provider['enabled'])
            # 自定义适配器允许匿名；端点/型号由共用准备验证，空凭据仍绑定原连接身份。
            credentials = bool(provider)
            facts = {'endpoint': (provider or {}).get('base_url'), 'capabilities': (provider or {}).get('capabilities')}
            connection = connection_binding(provider_id, custom=provider)
            policy = policy_api_response(None)
        else:
            provider = catalog.providers_by_id.get(provider_id)
            matches = [item for item in catalog.models if item.provider == provider_id and item.provider_model == model_id]
            model = matches[0] if len(matches) == 1 else None
            runtime = resolve_live_provider_runtime_config(provider_id)
            enabled = builtin_provider_enabled(provider_id)
            credentials = bool(runtime.api_key) or bool(provider and not provider.requires_key)
            facts = {'endpoint': runtime.base_url, 'model': asdict(model) if model else None}
            connection = connection_binding(provider_id, runtime=runtime)
            policy = policy_api_response(model.policy if model and model.policy else provider.policy if provider else None)
        if not enabled: blocked.append('provider_disabled')
        if not credentials: blocked.append('provider_credentials_missing')
        if provider_id in {'agnes_image', 'agnes_video'} and not assistant_allow_agnes(): blocked.append('agnes_generation_disabled')
        if policy['lifecycle'] == 'retired': blocked.append('model_retired')
        if model and (not model.selectable or model.status == 'reserved'): blocked.append('model_not_selectable')
        if model is None: acknowledgements.add('unverified_model')
        public.append({'provider': provider_id, 'model': model_id,
                       'capability_evidence': 'catalog_declaration' if model else 'custom_or_unverified',
                       'policy': policy, 'account_access_verified': False, 'generation_verified': False})
        bindings.append({'provider': provider_id, 'model': model_id, 'connection': connection,
                         'configuration': submission_intent_hash(facts)})
    if any(needs_reference_relay(provider, model, references) for provider, model in targets):
        relay = get_live_reference_relay_config()
        if relay.get('mode') != 'external_http' or not relay.get('upload_url'):
            blocked.append('reference_relay_unavailable')
        connection = connection_binding('reference_relay', relay=relay)
        bindings.append({'provider': 'reference_relay', 'model': None, 'connection': connection, 'configuration': connection})
    if any(item['kind'] == 'remote_url' for item in references): acknowledgements.add('mutable_remote_reference')
    return {'allowed': not blocked, 'blocked_reasons': sorted(set(blocked)), 'routes': public,
            'cost': {'amount': None, 'currency': None, 'estimate_status': 'unavailable', 'may_incur_cost': True},
            'required_acknowledgements': sorted(acknowledgements)}, bindings
