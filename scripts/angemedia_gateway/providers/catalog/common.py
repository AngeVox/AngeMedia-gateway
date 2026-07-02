"""Shared catalog parser validation helpers."""
from __future__ import annotations

import re
from typing import Any

from .errors import CatalogValidationError
from .schema import VALID_CAPABILITIES


SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SAFE_ADAPTER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
SIZE_PRESET_RE = re.compile(r"^[1-9]\d{1,3}x[1-9]\d{1,3}$")
ASPECT_RATIO_PRESET_RE = re.compile(r"^[1-9]\d{0,3}:[1-9]\d{0,3}$")
OPERATION_PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


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
