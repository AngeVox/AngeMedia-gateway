"""Top-level provider and model catalog item parsing."""
from __future__ import annotations

from typing import Any

from .common import (
    SAFE_ADAPTER_ID_RE,
    _capabilities,
    _dict,
    _optional_int,
    _optional_string,
    _reject_unknown_keys,
    _require_bool,
    _require_keys,
    _require_safe_id,
    _require_status,
    _require_string,
    _size_presets,
    _string_tuple,
)
from .errors import CatalogValidationError
from .operations import _operations
from .params import _param_specs, _ref_input_spec, _size_spec
from .schema import (
    ModelCatalogEntry,
    ProviderCatalogEntry,
    VALID_MEDIA_TYPES,
    VALID_MODEL_STATUSES,
    VALID_PROVIDER_STATUSES,
)


PROVIDER_KEYS = {
    "id",
    "display_name",
    "media_types",
    "status",
    "enabled_default",
    "config_enabled_key",
    "requires_key",
    "credential_keys",
    "adapter_id",
    "ui_group",
    "notes",
}
MODEL_REQUIRED_KEYS = {
    "id",
    "provider",
    "provider_model",
    "media_type",
    "display_name",
    "aliases",
    "status",
    "selectable",
    "default_chain_order",
    "capabilities",
    "params",
    "size_presets",
    "ref_inputs",
    "extra_allowlist",
    "tags",
}
MODEL_KEYS = MODEL_REQUIRED_KEYS | {"param_specs", "size", "ref_input_spec", "operations"}


def _parse_providers(raw_items: Any) -> list[ProviderCatalogEntry]:
    if not isinstance(raw_items, list):
        raise CatalogValidationError("providers must be a list")

    seen: set[str] = set()
    providers: list[ProviderCatalogEntry] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise CatalogValidationError(f"provider[{index}] must be a mapping")
        _reject_unknown_keys(f"provider[{index}]", raw, PROVIDER_KEYS)
        _require_keys(f"provider[{index}]", raw, PROVIDER_KEYS)

        provider_id = _require_safe_id(f"provider[{index}].id", raw["id"])
        if provider_id in seen:
            raise CatalogValidationError(f"duplicate provider id: {provider_id}")
        seen.add(provider_id)

        media_types = _string_tuple(f"provider[{index}].media_types", raw["media_types"])
        if not media_types:
            raise CatalogValidationError(f"provider {provider_id} must declare at least one media type")
        invalid_media = set(media_types) - VALID_MEDIA_TYPES
        if invalid_media:
            raise CatalogValidationError(f"provider {provider_id} has invalid media type: {sorted(invalid_media)[0]}")

        status = _require_status(f"provider[{index}].status", raw["status"], VALID_PROVIDER_STATUSES)
        enabled_default = _require_bool(f"provider[{index}].enabled_default", raw["enabled_default"])
        if status == "reserved" and enabled_default:
            raise CatalogValidationError(f"reserved provider {provider_id} cannot be enabled by default")

        adapter_id = _require_string(f"provider[{index}].adapter_id", raw["adapter_id"])
        if not SAFE_ADAPTER_ID_RE.match(adapter_id):
            raise CatalogValidationError(f"provider {provider_id} adapter_id must be a safe registry id")

        providers.append(
            ProviderCatalogEntry(
                id=provider_id,
                display_name=_require_string(f"provider[{index}].display_name", raw["display_name"]),
                media_types=media_types,
                status=status,
                enabled_default=enabled_default,
                config_enabled_key=_optional_string(f"provider[{index}].config_enabled_key", raw["config_enabled_key"]),
                requires_key=_require_bool(f"provider[{index}].requires_key", raw["requires_key"]),
                credential_keys=_string_tuple(f"provider[{index}].credential_keys", raw["credential_keys"]),
                adapter_id=adapter_id,
                ui_group=_require_string(f"provider[{index}].ui_group", raw["ui_group"]),
                notes=_require_string(f"provider[{index}].notes", raw["notes"]),
            )
        )
    return providers


def _parse_models(raw_items: Any, providers: list[ProviderCatalogEntry]) -> list[ModelCatalogEntry]:
    if not isinstance(raw_items, list):
        raise CatalogValidationError("models must be a list")

    providers_by_id = {provider.id: provider for provider in providers}
    seen: set[str] = set()
    models: list[ModelCatalogEntry] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise CatalogValidationError(f"model[{index}] must be a mapping")
        _reject_unknown_keys(f"model[{index}]", raw, MODEL_KEYS)
        _require_keys(f"model[{index}]", raw, MODEL_REQUIRED_KEYS)

        model_id = _require_safe_id(f"model[{index}].id", raw["id"])
        if model_id in seen:
            raise CatalogValidationError(f"duplicate model id: {model_id}")
        seen.add(model_id)

        provider_id = _require_safe_id(f"model[{index}].provider", raw["provider"])
        provider = providers_by_id.get(provider_id)
        if provider is None:
            raise CatalogValidationError(f"model {model_id} references unknown provider: {provider_id}")

        media_type = _require_status(f"model[{index}].media_type", raw["media_type"], VALID_MEDIA_TYPES)
        if media_type not in provider.media_types:
            raise CatalogValidationError(f"model {model_id} media_type is not supported by provider {provider_id}")

        status = _require_status(f"model[{index}].status", raw["status"], VALID_MODEL_STATUSES)
        selectable = _require_bool(f"model[{index}].selectable", raw["selectable"])
        default_chain_order = _optional_int(f"model[{index}].default_chain_order", raw["default_chain_order"])
        if default_chain_order is not None and (status != "release" or provider.status != "release"):
            raise CatalogValidationError(f"model {model_id} cannot enter default chain unless provider and model are release")
        if status == "reserved" and selectable:
            raise CatalogValidationError(f"reserved model {model_id} cannot be selectable")

        capabilities = _capabilities(f"model[{index}].capabilities", raw["capabilities"])
        params = _dict(f"model[{index}].params", raw["params"])
        size_presets = _size_presets(f"model[{index}].size_presets", raw["size_presets"])
        ref_inputs = _dict(f"model[{index}].ref_inputs", raw["ref_inputs"])
        param_specs = _param_specs(
            f"model[{index}].param_specs",
            raw.get("param_specs"),
            params,
        )
        size = _size_spec(
            f"model[{index}].size",
            raw.get("size"),
            size_presets,
        )
        ref_input_spec = _ref_input_spec(
            f"model[{index}].ref_input_spec",
            raw.get("ref_input_spec"),
            ref_inputs,
        )
        operations = _operations(f"model[{index}].operations", raw.get("operations"))
        models.append(
            ModelCatalogEntry(
                id=model_id,
                provider=provider_id,
                provider_model=_require_string(f"model[{index}].provider_model", raw["provider_model"]),
                media_type=media_type,
                display_name=_require_string(f"model[{index}].display_name", raw["display_name"]),
                aliases=_string_tuple(f"model[{index}].aliases", raw["aliases"]),
                status=status,
                selectable=selectable,
                default_chain_order=default_chain_order,
                capabilities=capabilities,
                params=params,
                param_specs=param_specs,
                size_presets=size_presets,
                size=size,
                ref_inputs=ref_inputs,
                ref_input_spec=ref_input_spec,
                operations=operations,
                extra_allowlist=_string_tuple(f"model[{index}].extra_allowlist", raw["extra_allowlist"]),
                tags=_string_tuple(f"model[{index}].tags", raw["tags"]),
            )
        )
    return models
