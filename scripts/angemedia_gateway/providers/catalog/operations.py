"""Catalog operation spec parsing."""
from __future__ import annotations

from typing import Any

from .common import (
    ASPECT_RATIO_PRESET_RE,
    OPERATION_PARAM_NAME_RE,
    SIZE_PRESET_RE,
    _dict,
    _optional_string,
    _require_bool,
    _require_keys,
    _require_status,
    _require_string,
    _reject_unknown_keys,
    _string_tuple,
)
from .errors import CatalogValidationError
from .params import _enum_values, _optional_number, _optional_positive_int
from .schema import (
    OperationParamSpec,
    OperationPreset,
    OperationRefSpec,
    OperationSpec,
    VALID_ASPECT_RATIO_MODES,
    VALID_OPERATIONS,
    VALID_OPERATION_EVIDENCE,
    VALID_OPERATION_PARAM_KINDS,
    VALID_REF_PROVIDER_FORMATS,
    VALID_SIZE_MODES,
)


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
        if kind == "size" and not (
            SIZE_PRESET_RE.match(preset_value) or preset_value in {"1K", "2K", "3K", "4K"}
        ):
            raise CatalogValidationError(
                f"{label}[{index}].value must use WIDTHxHEIGHT format or a supported named tier"
            )
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
