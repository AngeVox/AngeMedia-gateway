"""Catalog-driven request validation for model operations."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .loader import load_provider_catalog
from .schema import ModelCatalogEntry, OperationParamSpec, ProviderCatalog


class CatalogOperationValidationError(ValueError):
    """Raised when a request violates catalog operation capability bounds."""


def validate_image_operation_request(
    req: Any,
    targets: Iterable[Any],
    *,
    catalog: ProviderCatalog | None = None,
) -> None:
    """Validate image requests against operation specs for resolved catalog targets.

    Unknown/raw fallback targets and models with no operation metadata are left
    untouched so legacy ModelScope, Agnes, and custom-provider flows keep their
    current behavior.
    """

    loaded_catalog = catalog or load_provider_catalog()
    for target in targets:
        model = _catalog_model_for_target(loaded_catalog, target)
        if model is None:
            continue
        validate_operation_params(req, model, "text_to_image")


def validate_operation_params(req: Any, model: ModelCatalogEntry, operation_name: str) -> None:
    operation = model.operations.get(operation_name)
    if operation is None or not operation.supported:
        return
    for param_name, spec in operation.params.items():
        value = _request_value(req, param_name)
        if value is None:
            if spec.required:
                raise CatalogOperationValidationError(
                    f"{model.id}.{operation_name}.{param_name} is required"
                )
            continue
        _validate_operation_value(model, operation_name, param_name, spec, value)


def operation_provider_field_map(model: ModelCatalogEntry, operation_name: str) -> dict[str, str]:
    operation = model.operations.get(operation_name)
    if operation is None:
        return {}
    return {
        param_name: str(spec.provider_field)
        for param_name, spec in operation.params.items()
        if spec.provider_field
    }


def _catalog_model_for_target(catalog: ProviderCatalog, target: Any) -> ModelCatalogEntry | None:
    provider = _target_value(target, "provider")
    provider_model = _target_value(target, "model")
    for model in catalog.models:
        if model.provider == provider and model.provider_model == provider_model:
            return model
    return None


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _request_value(req: Any, name: str) -> Any:
    if isinstance(req, Mapping):
        return req.get(name)
    return getattr(req, name, None)


def _validate_operation_value(
    model: ModelCatalogEntry,
    operation_name: str,
    param_name: str,
    spec: OperationParamSpec,
    value: Any,
) -> None:
    label = f"{model.id}.{operation_name}.{param_name}"
    if spec.kind == "string":
        if not isinstance(value, str):
            raise CatalogOperationValidationError(f"{label} must be a string")
    elif spec.kind in {"int", "seed"}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise CatalogOperationValidationError(f"{label} must be an integer")
        _validate_numeric_bounds(label, spec, value)
    elif spec.kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CatalogOperationValidationError(f"{label} must be a number")
        _validate_numeric_bounds(label, spec, float(value))
    elif spec.kind == "bool":
        if not isinstance(value, bool):
            raise CatalogOperationValidationError(f"{label} must be a boolean")
    elif spec.kind == "enum":
        if value not in spec.enum_values:
            raise CatalogOperationValidationError(f"{label} has unsupported value")
    elif spec.kind == "size":
        _validate_size(label, spec, value)
    else:
        raise CatalogOperationValidationError(f"{label} has unsupported param kind")


def _validate_numeric_bounds(label: str, spec: OperationParamSpec, value: int | float) -> None:
    if spec.min is not None and value < spec.min:
        raise CatalogOperationValidationError(f"{label} must be greater than or equal to {spec.min}")
    if spec.max is not None and value > spec.max:
        raise CatalogOperationValidationError(f"{label} must be less than or equal to {spec.max}")


def _validate_size(label: str, spec: OperationParamSpec, value: Any) -> None:
    if not isinstance(value, str):
        raise CatalogOperationValidationError(f"{label} must be a WIDTHxHEIGHT string")
    if spec.mode == "preset":
        allowed = {preset.value for preset in spec.presets}
        if value not in allowed:
            raise CatalogOperationValidationError(f"{label} has unsupported preset")
