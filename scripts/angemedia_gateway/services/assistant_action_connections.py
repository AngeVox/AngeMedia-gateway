"""把端点及凭据身份绑定到动作；HMAC密钥只在本数据库使用，不公开指纹材料。"""
from __future__ import annotations

from ..providers.reference_delivery import PUBLIC_URL, builtin_reference_delivery_capabilities
from .media_connection_binding import binding_digest, connection_binding


def needs_reference_relay(provider, model, references):
    if provider.startswith('custom:') or not any(item['kind'] == 'gateway_image' for item in references):
        return False
    return builtin_reference_delivery_capabilities(provider, model).methods == (PUBLIC_URL,)
