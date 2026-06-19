"""Load and validate the local static provider catalog."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .schema import (
    OperationParamSpec,
    OperationPreset,
    OperationRefSpec,
    OperationSpec,
    ParamSpec,
    ProviderCatalog,
    ProviderCatalogEntry,
    RefInputSpec,
    SizeSpec,
    ModelCatalogEntry,
    VALID_ASPECT_RATIO_MODES,
    VALID_CAPABILITIES,
    VALID_MEDIA_TYPES,
    VALID_MODEL_STATUSES,
    VALID_OPERATIONS,
    VALID_OPERATION_EVIDENCE,
    VALID_OPERATION_PARAM_KINDS,
    VALID_PARAM_KINDS,
    VALID_PROVIDER_STATUSES,
    VALID_REF_PROVIDER_FORMATS,
    VALID_SIZE_MODES,
)


CATALOG_DIR = Path(__file__).resolve().parent
PROVIDERS_FILE = "providers.yaml"
MODELS_FILE = "models.yaml"

PROVIDERS_TOP_KEYS = {"providers"}
MODELS_TOP_KEYS = {"models"}
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
PARAM_SPEC_KEYS = {"kind", "default", "min", "max", "enum_values"}
SIZE_SPEC_KEYS = {
    "mode",
    "presets",
    "min_width",
    "max_width",
    "min_height",
    "max_height",
    "min_pixels",
    "max_pixels",
    "multiple_of",
}
REF_INPUT_SPEC_KEYS = {"roles", "max_total", "formats", "required"}
OPERATION_KEYS = {"supported", "params", "refs"}
OPERATION_PARAM_KEYS = {
    "kind",
    "required",
    "provider_field",
    "evidence",
    "default",
    "min",
    "max",
    "enum_values",
    "mode",
    "presets",
    "allow_with_size",
}
OPERATION_PRESET_KEYS = {"value", "label"}
OPERATION_REF_KEYS = {
    "role", "roles", "provider_field", "max_count", "max_total", "formats", "provider_format", "required",
}
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SAFE_ADAPTER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
SIZE_PRESET_RE = re.compile(r"^[1-9]\d{1,3}x[1-9]\d{1,3}$")
ASPECT_RATIO_PRESET_RE = re.compile(r"^[1-9]\d{0,3}:[1-9]\d{0,3}$")
OPERATION_PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class CatalogValidationError(ValueError):
    """Raised when the static provider catalog is invalid."""


def load_provider_catalog(catalog_dir: Path | None = None) -> ProviderCatalog:
    base_dir = catalog_dir or CATALOG_DIR
    providers_raw = _load_yaml_mapping(base_dir / PROVIDERS_FILE, allowed_top_keys=PROVIDERS_TOP_KEYS)
    models_raw = _load_yaml_mapping(base_dir / MODELS_FILE, allowed_top_keys=MODELS_TOP_KEYS)
    providers = _parse_providers(providers_raw.get("providers"))
    models = _parse_models(models_raw.get("models"), providers)
    return ProviderCatalog(providers=tuple(providers), models=tuple(models))


def _load_yaml_mapping(path: Path, *, allowed_top_keys: set[str]) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise CatalogValidationError(f"{path.name} must contain a mapping")
    _reject_unknown_keys(path.name, data, allowed_top_keys)
    return data


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


def _reject_unknown_keys(label: str, value: dict[str, Any], allowed_keys: set[str]) -> None:
    unknown = set(value) - allowed_keys
    if unknown:
        raise CatalogValidationError(f"{label} has unknown key: {sorted(unknown)[0]}")


def _require_keys(label: str, value: dict[str, Any], required_keys: set[str]) -> None:
    missing = required_keys - set(value)
    if missing:
        raise CatalogValidationError(f"{label} is missing key: {sorted(missing)[0]}")


def _require_safe_id(label: str, value: Any) -> str:
    text = _require_string(label, value)
    if not SAFE_ID_RE.match(text):
        raise CatalogValidationError(f"{label} must be a safe id")
    return text


def _require_status(label: str, value: Any, allowed: set[str]) -> str:
    text = _require_string(label, value)
    if text not in allowed:
        raise CatalogValidationError(f"{label} has invalid value: {text}")
    return text


def _require_string(label: str, value: Any) -> str:
    if not isinstance(value, str):
        raise CatalogValidationError(f"{label} must be a string")
    text = value.strip()
    if not text:
        raise CatalogValidationError(f"{label} must not be empty")
    return text


def _optional_string(label: str, value: Any) -> str | None:
    if value is None:
        return None
    return _require_string(label, value)


def _require_bool(label: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise CatalogValidationError(f"{label} must be a boolean")
    return value


def _optional_int(label: str, value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise CatalogValidationError(f"{label} must be an integer or null")
    return value


def _string_tuple(label: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CatalogValidationError(f"{label} must be a list")
    items: list[str] = []
    for index, item in enumerate(value):
        items.append(_require_string(f"{label}[{index}]", item))
    return tuple(items)


def _size_presets(label: str, value: Any) -> tuple[str, ...]:
    presets = _string_tuple(label, value)
    for index, preset in enumerate(presets):
        if not SIZE_PRESET_RE.match(preset):
            raise CatalogValidationError(f"{label}[{index}] must use WIDTHxHEIGHT format")
    return presets


def _dict(label: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CatalogValidationError(f"{label} must be a mapping")
    return dict(value)


def _capabilities(label: str, value: Any) -> dict[str, bool]:
    data = _dict(label, value)
    unknown = set(data) - VALID_CAPABILITIES
    if unknown:
        raise CatalogValidationError(f"{label} has unknown capability: {sorted(unknown)[0]}")
    for key, item in data.items():
        if not isinstance(item, bool):
            raise CatalogValidationError(f"{label}.{key} must be a boolean")
    return dict(data)


def _param_specs(label: str, value: Any, legacy_params: dict[str, Any]) -> dict[str, ParamSpec]:
    if value is None:
        return _legacy_param_specs(label, legacy_params)
    data = _dict(label, value)
    specs: dict[str, ParamSpec] = {}
    for name, raw_spec in data.items():
        spec_name = _require_string(f"{label} key", name)
        specs[spec_name] = _param_spec(f"{label}.{spec_name}", raw_spec)
    return specs


def _legacy_param_specs(label: str, legacy_params: dict[str, Any]) -> dict[str, ParamSpec]:
    specs: dict[str, ParamSpec] = {}
    for name, raw_spec in legacy_params.items():
        spec_name = _require_string(f"{label} key", name)
        if isinstance(raw_spec, str):
            specs[spec_name] = _param_spec_from_kind(f"{label}.{spec_name}", raw_spec)
        elif isinstance(raw_spec, dict):
            specs[spec_name] = _param_spec(f"{label}.{spec_name}", raw_spec)
        elif isinstance(raw_spec, list):
            if not raw_spec:
                raise CatalogValidationError(f"{label}.{spec_name} enum values must not be empty")
            specs[spec_name] = ParamSpec(
                kind="enum",
                default=None,
                min=None,
                max=None,
                enum_values=tuple(raw_spec),
            )
        else:
            raise CatalogValidationError(f"{label}.{spec_name} must be a string, mapping, or list")
    return specs


def _param_spec(label: str, value: Any) -> ParamSpec:
    if isinstance(value, str):
        return _param_spec_from_kind(label, value)
    data = _dict(label, value)
    _reject_unknown_keys(label, data, PARAM_SPEC_KEYS)
    if "kind" not in data:
        raise CatalogValidationError(f"{label} is missing key: kind")
    kind = _param_kind(f"{label}.kind", data["kind"])
    enum_values = _enum_values(f"{label}.enum_values", data.get("enum_values", []))
    if kind == "enum" and not enum_values:
        raise CatalogValidationError(f"{label}.enum_values must not be empty for enum params")
    min_value = _optional_number(f"{label}.min", data.get("min"))
    max_value = _optional_number(f"{label}.max", data.get("max"))
    if min_value is not None and max_value is not None and min_value > max_value:
        raise CatalogValidationError(f"{label}.min must be less than or equal to max")
    return ParamSpec(
        kind=kind,
        default=data.get("default"),
        min=min_value,
        max=max_value,
        enum_values=enum_values,
    )


def _param_spec_from_kind(label: str, value: Any) -> ParamSpec:
    raw_kind = _require_string(label, value)
    kind = {
        "integer": "int",
        "number": "float",
        "boolean": "bool",
    }.get(raw_kind, raw_kind)
    return ParamSpec(
        kind=_param_kind(label, kind),
        default=None,
        min=None,
        max=None,
        enum_values=(),
    )


def _param_kind(label: str, value: Any) -> str:
    kind = _require_string(label, value)
    if kind not in VALID_PARAM_KINDS:
        raise CatalogValidationError(f"{label} has invalid param kind: {kind}")
    return kind


def _enum_values(label: str, value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CatalogValidationError(f"{label} must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, (str, int, float, bool)) or item is None:
            raise CatalogValidationError(f"{label}[{index}] must be a scalar")
    return tuple(value)


def _size_spec(label: str, value: Any, legacy_presets: tuple[str, ...]) -> SizeSpec:
    if value is None:
        return SizeSpec(
            mode="preset" if legacy_presets else "freeform",
            presets=legacy_presets,
            min_width=None,
            max_width=None,
            min_height=None,
            max_height=None,
            min_pixels=None,
            max_pixels=None,
            multiple_of=None,
        )
    data = _dict(label, value)
    _reject_unknown_keys(label, data, SIZE_SPEC_KEYS)
    if "mode" not in data:
        raise CatalogValidationError(f"{label} is missing key: mode")
    mode = _require_status(f"{label}.mode", data["mode"], VALID_SIZE_MODES)
    presets = _size_presets(f"{label}.presets", data.get("presets", list(legacy_presets)))
    if mode == "preset" and not presets:
        raise CatalogValidationError(f"{label}.presets must not be empty for preset size mode")
    if legacy_presets and presets and presets != legacy_presets:
        raise CatalogValidationError(f"{label}.presets must match size_presets")

    min_width = _optional_positive_int(f"{label}.min_width", data.get("min_width"))
    max_width = _optional_positive_int(f"{label}.max_width", data.get("max_width"))
    min_height = _optional_positive_int(f"{label}.min_height", data.get("min_height"))
    max_height = _optional_positive_int(f"{label}.max_height", data.get("max_height"))
    min_pixels = _optional_positive_int(f"{label}.min_pixels", data.get("min_pixels"))
    max_pixels = _optional_positive_int(f"{label}.max_pixels", data.get("max_pixels"))
    multiple_of = _optional_positive_int(f"{label}.multiple_of", data.get("multiple_of"))
    _check_min_max(label, "width", min_width, max_width)
    _check_min_max(label, "height", min_height, max_height)
    _check_min_max(label, "pixels", min_pixels, max_pixels)
    return SizeSpec(
        mode=mode,
        presets=presets,
        min_width=min_width,
        max_width=max_width,
        min_height=min_height,
        max_height=max_height,
        min_pixels=min_pixels,
        max_pixels=max_pixels,
        multiple_of=multiple_of,
    )


def _ref_input_spec(label: str, value: Any, legacy_ref_inputs: dict[str, Any]) -> RefInputSpec:
    if value is None:
        return RefInputSpec(
            roles=tuple(str(role) for role in legacy_ref_inputs.keys()),
            max_total=None,
            formats=(),
            required=any(str(value).lower() == "required" for value in legacy_ref_inputs.values()),
        )
    data = _dict(label, value)
    _reject_unknown_keys(label, data, REF_INPUT_SPEC_KEYS)
    roles = _string_tuple(f"{label}.roles", data.get("roles", []))
    max_total = _optional_positive_int(f"{label}.max_total", data.get("max_total"))
    formats = _string_tuple(f"{label}.formats", data.get("formats", []))
    required = _require_bool(f"{label}.required", data.get("required", False))
    if required and not roles:
        raise CatalogValidationError(f"{label}.roles must not be empty when required is true")
    return RefInputSpec(roles=roles, max_total=max_total, formats=formats, required=required)


def _operations(label: str, value: Any) -> dict[str, OperationSpec]:
    if value is None:
        return {}
    data = _dict(label, value)
    unknown = set(data) - VALID_OPERATIONS
    if unknown:
        raise CatalogValidationError(f"{label} has unknown operation: {sorted(unknown)[0]}")
    operations: dict[str, OperationSpec] = {}
    for operation_name, raw_operation in data.items():
        name = _require_string(f"{label} key", operation_name)
        operations[name] = _operation_spec(f"{label}.{name}", raw_operation)
    return operations


def _operation_spec(label: str, value: Any) -> OperationSpec:
    data = _dict(label, value)
    _reject_unknown_keys(label, data, OPERATION_KEYS)
    _require_keys(label, data, OPERATION_KEYS)
    supported = _require_bool(f"{label}.supported", data["supported"])
    params = _operation_params(f"{label}.params", data["params"])
    refs = _operation_refs(f"{label}.refs", data["refs"])
    return OperationSpec(supported=supported, params=params, refs=refs)


def _operation_params(label: str, value: Any) -> dict[str, OperationParamSpec]:
    data = _dict(label, value)
    params: dict[str, OperationParamSpec] = {}
    for param_name, raw_spec in data.items():
        name = _operation_param_name(f"{label} key", param_name)
        params[name] = _operation_param_spec(f"{label}.{name}", name, raw_spec)
    return params


def _operation_param_name(label: str, value: Any) -> str:
    text = _require_string(label, value)
    if not OPERATION_PARAM_NAME_RE.match(text):
        raise CatalogValidationError(f"{label} must be a safe operation param name")
    return text


def _operation_param_spec(label: str, name: str, value: Any) -> OperationParamSpec:
    data = _dict(label, value)
    _reject_unknown_keys(label, data, OPERATION_PARAM_KEYS)
    if "kind" not in data:
        raise CatalogValidationError(f"{label} is missing key: kind")
    if "evidence" not in data:
        raise CatalogValidationError(f"{label} is missing key: evidence")
    kind = _operation_param_kind(f"{label}.kind", data["kind"])
    evidence = _require_status(f"{label}.evidence", data["evidence"], VALID_OPERATION_EVIDENCE)
    provider_field = _optional_string(f"{label}.provider_field", data.get("provider_field"))
    if name == "aspect_ratio" and provider_field is None:
        provider_field = "aspect_ratio"
    if name != "prompt" and provider_field is None:
        raise CatalogValidationError(f"{label}.provider_field is required for non-prompt params")

    enum_values = _enum_values(f"{label}.enum_values", data.get("enum_values", []))
    if kind == "enum" and not enum_values:
        raise CatalogValidationError(f"{label}.enum_values must not be empty for enum params")
    min_value = _optional_number(f"{label}.min", data.get("min"))
    max_value = _optional_number(f"{label}.max", data.get("max"))
    if min_value is not None and max_value is not None and min_value > max_value:
        raise CatalogValidationError(f"{label}.min must be less than or equal to max")

    mode = _optional_operation_size_mode(f"{label}.mode", data.get("mode"))
    presets = _operation_presets(f"{label}.presets", data.get("presets"), kind=kind)
    if kind == "size":
        if mode is None:
            raise CatalogValidationError(f"{label}.mode is required for size params")
        if mode == "preset" and not presets:
            raise CatalogValidationError(f"{label}.presets must not be empty for preset size params")
    elif kind == "aspect_ratio":
        if mode not in VALID_ASPECT_RATIO_MODES:
            raise CatalogValidationError(f"{label}.mode must be preset for aspect_ratio params")
        if not presets:
            raise CatalogValidationError(f"{label}.presets must not be empty for aspect_ratio params")
    elif mode is not None or presets:
        raise CatalogValidationError(f"{label}.mode and presets are only valid for size or aspect_ratio params")

    default = data.get("default")
    if kind == "aspect_ratio" and default is not None:
        default = _require_string(f"{label}.default", default)
        if default not in {preset.value for preset in presets}:
            raise CatalogValidationError(f"{label}.default must match an aspect_ratio preset")
    allow_with_size = _require_bool(f"{label}.allow_with_size", data.get("allow_with_size", False))
    if kind != "aspect_ratio" and "allow_with_size" in data:
        raise CatalogValidationError(f"{label}.allow_with_size is only valid for aspect_ratio params")

    return OperationParamSpec(
        kind=kind,
        required=_require_bool(f"{label}.required", data.get("required", False)),
        provider_field=provider_field,
        evidence=evidence,
        default=default,
        min=min_value,
        max=max_value,
        enum_values=enum_values,
        mode=mode,
        presets=presets,
        allow_with_size=allow_with_size,
    )


def _operation_param_kind(label: str, value: Any) -> str:
    kind = _require_string(label, value)
    if kind not in VALID_OPERATION_PARAM_KINDS:
        raise CatalogValidationError(f"{label} has invalid operation param kind: {kind}")
    return kind


def _optional_operation_size_mode(label: str, value: Any) -> str | None:
    if value is None:
        return None
    return _require_status(label, value, VALID_SIZE_MODES)


def _operation_presets(label: str, value: Any, *, kind: str) -> tuple[OperationPreset, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CatalogValidationError(f"{label} must be a list")
    presets: list[OperationPreset] = []
    for index, raw_item in enumerate(value):
        if not isinstance(raw_item, dict):
            raise CatalogValidationError(f"{label}[{index}] must be a mapping")
        _reject_unknown_keys(f"{label}[{index}]", raw_item, OPERATION_PRESET_KEYS)
        if "value" not in raw_item:
            raise CatalogValidationError(f"{label}[{index}] is missing key: value")
        preset_value = _require_string(f"{label}[{index}].value", raw_item["value"])
        if kind == "size" and not SIZE_PRESET_RE.match(preset_value):
            raise CatalogValidationError(f"{label}[{index}].value must use WIDTHxHEIGHT format")
        if kind == "aspect_ratio" and not ASPECT_RATIO_PRESET_RE.match(preset_value):
            raise CatalogValidationError(f"{label}[{index}].value must use WIDTH:HEIGHT format")
        presets.append(
            OperationPreset(
                value=preset_value,
                label=_optional_string(f"{label}[{index}].label", raw_item.get("label")),
            )
        )
    return tuple(presets)


def _operation_refs(label: str, value: Any) -> tuple[OperationRefSpec, ...]:
    if not isinstance(value, list):
        raise CatalogValidationError(f"{label} must be a list")
    refs: list[OperationRefSpec] = []
    for index, raw_ref in enumerate(value):
        if not isinstance(raw_ref, dict):
            raise CatalogValidationError(f"{label}[{index}] must be a mapping")
        _reject_unknown_keys(f"{label}[{index}]", raw_ref, OPERATION_REF_KEYS)
        if "role" in raw_ref and "roles" in raw_ref:
            raise CatalogValidationError(f"{label}[{index}] must use role or roles, not both")
        if "role" in raw_ref:
            roles = (_require_string(f"{label}[{index}].role", raw_ref["role"]),)
        elif "roles" in raw_ref:
            roles = _string_tuple(f"{label}[{index}].roles", raw_ref["roles"])
        else:
            raise CatalogValidationError(f"{label}[{index}] is missing key: role")
        if not roles:
            raise CatalogValidationError(f"{label}[{index}].roles must not be empty")
        if "max_count" in raw_ref and "max_total" in raw_ref:
            raise CatalogValidationError(f"{label}[{index}] must use max_count or max_total, not both")
        provider_field = _optional_string(f"{label}[{index}].provider_field", raw_ref.get("provider_field"))
        if provider_field and not OPERATION_PARAM_NAME_RE.match(provider_field):
            raise CatalogValidationError(f"{label}[{index}].provider_field must be a safe operation field")
        max_total = raw_ref.get("max_count", raw_ref.get("max_total"))
        provider_format = _optional_string(f"{label}[{index}].provider_format", raw_ref.get("provider_format"))
        if provider_format is not None and provider_format not in VALID_REF_PROVIDER_FORMATS:
            raise CatalogValidationError(f"{label}[{index}].provider_format has invalid value: {provider_format}")
        refs.append(
            OperationRefSpec(
                roles=roles,
                provider_field=provider_field,
                max_total=_optional_positive_int(f"{label}[{index}].max_count", max_total),
                formats=_string_tuple(f"{label}[{index}].formats", raw_ref.get("formats", [])),
                provider_format=provider_format,
                required=_require_bool(f"{label}[{index}].required", raw_ref.get("required", False)),
            )
        )
    return tuple(refs)


def _optional_number(label: str, value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CatalogValidationError(f"{label} must be a number or null")
    return value


def _optional_positive_int(label: str, value: Any) -> int | None:
    number = _optional_int(label, value)
    if number is not None and number <= 0:
        raise CatalogValidationError(f"{label} must be positive")
    return number


def _check_min_max(label: str, name: str, min_value: int | None, max_value: int | None) -> None:
    if min_value is not None and max_value is not None and min_value > max_value:
        raise CatalogValidationError(f"{label}.min_{name} must be less than or equal to max_{name}")
