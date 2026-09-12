"""Resolve persisted Provider transport configuration into a runtime decision."""
from __future__ import annotations

from ..repositories.provider_transport_config import get_provider_transport_config
from .transport_policy import ProviderTransportDecision, resolve_provider_transport


def resolve_saved_provider_transport(provider_id: str | None = None) -> ProviderTransportDecision:
    global_config = get_provider_transport_config(None)
    if provider_id is None:
        return resolve_provider_transport(global_config=global_config)

    provider_config = get_provider_transport_config(provider_id)
    return resolve_provider_transport(
        provider_config=provider_config,
        global_config=global_config,
    )
