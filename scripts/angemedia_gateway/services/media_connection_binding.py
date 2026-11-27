"""复用数据库专用HMAC绑定媒体端点、凭据和传输；不公开指纹材料。"""
from contextlib import closing
import hmac

from ..db.connection import db_connect
from ..job_submission import canonical_submission_json
from ..providers.runtime_config import resolve_live_provider_runtime_config
from ..providers.transport_config import resolve_live_provider_transport
from ..repositories.assistant_action_execution import read_binding_key
from ..repositories.reference_relay_config import get_live_reference_relay_config
from ..repositories.settings import get_custom_provider


def binding_digest(provider, facts):
    with closing(db_connect()) as conn:
        key = read_binding_key(conn)
    # 保持既有域与序列化，已有助手收据无需重签或降级。
    message = canonical_submission_json({'domain': 'media-action-connection-v1', 'provider': provider, 'facts': facts})
    return hmac.digest(key, message.encode('utf-8'), 'sha256').hex()


def connection_binding(provider, *, runtime=None, custom=None, relay=None, transport=None):
    if provider == 'reference_relay':
        facts = get_live_reference_relay_config() if relay is None else relay
    elif provider.startswith('custom:'):
        value = get_custom_provider(provider.split(':', 1)[1], include_secret=True) if custom is None else custom
        facts = {'endpoint': (value or {}).get('base_url'), 'credential': (value or {}).get('api_key')}
    else:
        value = resolve_live_provider_runtime_config(provider) if runtime is None else runtime
        facts = {'endpoint': value.base_url, 'credential': value.api_key}
    if provider != 'reference_relay':
        decision = transport if transport is not None else resolve_live_provider_transport(provider.removeprefix('custom:'))
        facts['transport'] = {'mode': decision.mode, 'proxy': decision.proxy_url}
    return binding_digest(provider, facts)
