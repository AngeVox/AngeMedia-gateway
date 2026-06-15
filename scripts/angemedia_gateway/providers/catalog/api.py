"""Safe API projection for the static provider catalog."""
from __future__ import annotations

from typing import Any

from .schema import (
    ModelCatalogEntry,
    OperationParamSpec,
    OperationRefSpec,
    OperationSizePreset,
    OperationSpec,
    ParamSpec,
    ProviderCatalog,
    ProviderCatalogEntry,
    RefInputSpec,
    SizeSpec,
)


def catalog_api_response(catalog: ProviderCatalog) -> dict[str, Any]:
    """Return the catalog fields that are safe and useful for admin UI clients."""

    return {
        "object": "provider_catalog",
        "providers": [_provider_entry(item, catalog) for item in catalog.providers],
        "models": [_model_entry(item) for item in catalog.models],
    }


def _provider_entry(provider: ProviderCatalogEntry, catalog: ProviderCatalog) -> dict[str, Any]:
    provider_models = [model for model in catalog.models if model.provider == provider.id]
    default_orders = [
        model.default_chain_order
        for model in provider_models
        if model.default_chain_order is not None
    ]
    media_types = list(provider.media_types)
    return {
        "id": provider.id,
        "display_name": provider.display_name,
        "media_type": media_types[0] if len(media_types) == 1 else "multi",
        "media_types": media_types,
        "status": provider.status,
        "enabled_default": provider.enabled_default,
        "config_enabled_key": provider.config_enabled_key,
        "requires_key": provider.requires_key,
        "adapter_id": provider.adapter_id,
        "ui_group": provider.ui_group,
        "selectable": any(model.selectable for model in provider_models),
        "default_chain_order": min(default_orders) if default_orders else None,
        "tags": [],
    }


def _model_entry(model: ModelCatalogEntry) -> dict[str, Any]:
    return {
        "id": model.id,
        "provider_id": model.provider,
        "provider_model": model.provider_model,
        "media_type": model.media_type,
        "display_name": model.display_name,
        "aliases": list(model.aliases),
        "status": model.status,
        "selectable": model.selectable,
        "default_chain_order": model.default_chain_order,
        "capabilities": dict(model.capabilities),
        "params": dict(model.params),
        "param_specs": {key: _param_spec(value) for key, value in model.param_specs.items()},
        "size_presets": list(model.size_presets),
        "size": _size_spec(model.size),
        "ref_inputs": dict(model.ref_inputs),
        "ref_input_spec": _ref_input_spec(model.ref_input_spec),
        "operations": {key: _operation_spec(value) for key, value in model.operations.items()},
        "extra_allowlist": list(model.extra_allowlist),
        "tags": list(model.tags),
    }


def _param_spec(spec: ParamSpec) -> dict[str, Any]:
    return {
        "kind": spec.kind,
        "default": spec.default,
        "min": spec.min,
        "max": spec.max,
        "enum_values": list(spec.enum_values),
    }


def _size_spec(spec: SizeSpec) -> dict[str, Any]:
    return {
        "mode": spec.mode,
        "presets": list(spec.presets),
        "min_width": spec.min_width,
        "max_width": spec.max_width,
        "min_height": spec.min_height,
        "max_height": spec.max_height,
        "multiple_of": spec.multiple_of,
    }


def _ref_input_spec(spec: RefInputSpec) -> dict[str, Any]:
    return {
        "roles": list(spec.roles),
        "max_total": spec.max_total,
        "formats": list(spec.formats),
        "required": spec.required,
    }


def _operation_spec(spec: OperationSpec) -> dict[str, Any]:
    return {
        "supported": spec.supported,
        "params": {key: _operation_param_spec(value) for key, value in spec.params.items()},
        "refs": [_operation_ref_spec(item) for item in spec.refs],
    }


def _operation_param_spec(spec: OperationParamSpec) -> dict[str, Any]:
    return {
        "kind": spec.kind,
        "required": spec.required,
        "provider_field": spec.provider_field,
        "evidence": spec.evidence,
        "default": spec.default,
        "min": spec.min,
        "max": spec.max,
        "enum_values": list(spec.enum_values),
        "mode": spec.mode,
        "presets": [_operation_size_preset(item) for item in spec.presets],
    }


def _operation_size_preset(spec: OperationSizePreset) -> dict[str, Any]:
    return {
        "value": spec.value,
        "label": spec.label,
    }


def _operation_ref_spec(spec: OperationRefSpec) -> dict[str, Any]:
    return {
        "roles": list(spec.roles),
        "provider_field": spec.provider_field,
        "max_count": spec.max_total,
        "max_total": spec.max_total,
        "formats": list(spec.formats),
        "required": spec.required,
    }
