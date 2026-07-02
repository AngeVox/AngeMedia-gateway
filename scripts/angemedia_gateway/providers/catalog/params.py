"""Catalog model parameter, size, and reference input parsing."""
from __future__ import annotations

from typing import Any

from .common import (
    _dict,
    _optional_int,
    _require_bool,
    _require_status,
    _require_string,
    _reject_unknown_keys,
    _size_presets,
    _string_tuple,
)
from .errors import CatalogValidationError
from .schema import ParamSpec, RefInputSpec, SizeSpec, VALID_PARAM_KINDS, VALID_SIZE_MODES


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
