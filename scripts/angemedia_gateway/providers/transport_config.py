"""Resolve persisted Provider transport configuration into a runtime decision."""
from __future__ import annotations

from ..repositories.provider_transport_config import get_provider_transport_config
from .transport_policy import ProviderTransportDecision, resolve_provider_transport
from ..media_execution_scope import current_execution_scope


def resolve_saved_provider_transport(provider_id: str | None = None) -> ProviderTransportDecision:
    scope = current_execution_scope()
    return scope.transport(provider_id) if scope is not None else resolve_live_provider_transport(provider_id)


def resolve_live_provider_transport(provider_id: str | None = None) -> ProviderTransportDecision:
    """动作校验核对当前传输策略，实际请求读取其单次调用快照。"""
    global_config = get_provider_transport_config(None)
    if provider_id is None:
        return resolve_provider_transport(global_config=global_config)

    provider_config = get_provider_transport_config(provider_id)
    return resolve_provider_transport(
        provider_config=provider_config,
        global_config=global_config,
    )
