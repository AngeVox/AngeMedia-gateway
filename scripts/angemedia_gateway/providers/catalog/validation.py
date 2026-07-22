"""Catalog-driven request validation for model operations."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlparse

from ...reference_images import is_safe_image_data_url
from .loader import load_provider_catalog
from .schema import ModelCatalogEntry, OperationParamSpec, OperationRefSpec, ProviderCatalog, SizeSpec


IMAGE_OPERATION_PARAM_NAMES = frozenset({
    "prompt", "size", "aspect_ratio", "negative_prompt", "seed", "steps", "guidance",
})
IMAGE_REFERENCE_REQUEST_FIELDS = frozenset({
    "image", "images", "input_image", "input_images", "init_image",
    "reference_image", "reference_images", "control_image", "mask", "mask_image",
})
SIZE_VALUE_RE = re.compile(r"^([1-9]\d{1,3})x([1-9]\d{1,3})$")


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
        operation_name = image_operation_for_request(req, model)
        validate_operation_params(req, model, operation_name)
        validate_operation_refs(req, model, operation_name)


def image_operation_for_request(req: Any, model: ModelCatalogEntry) -> str:
    if any(_has_request_value(req, field) for field in IMAGE_REFERENCE_REQUEST_FIELDS):
        if _operation_supported(model, "image_to_image"):
            return "image_to_image"
        if model.operations:
            raise CatalogOperationValidationError(f"{model.id} does not support image references")
    return "text_to_image"


def validate_operation_params(req: Any, model: ModelCatalogEntry, operation_name: str) -> None:
    operation = model.operations.get(operation_name)
    if operation is None or not operation.supported:
        return
    for param_name in IMAGE_OPERATION_PARAM_NAMES - operation.params.keys():
        if _has_request_value(req, param_name):
            raise CatalogOperationValidationError(f"{model.id}.{operation_name}.{param_name} is not supported")
    aspect_ratio_spec = operation.params.get("aspect_ratio")
    if (
        aspect_ratio_spec is not None
        and not aspect_ratio_spec.allow_with_size
        and _has_request_value(req, "aspect_ratio")
        and _has_request_value(req, "size")
    ):
        raise CatalogOperationValidationError(
            f"{model.id}.{operation_name}.size and aspect_ratio cannot be used together"
        )
    for param_name, spec in operation.params.items():
        value = _request_value(req, param_name)
        if value is None:
            if spec.required:
                raise CatalogOperationValidationError(
                    f"{model.id}.{operation_name}.{param_name} is required"
                )
            continue
        _validate_operation_value(model, operation_name, param_name, spec, value)


def validate_operation_refs(req: Any, model: ModelCatalogEntry, operation_name: str) -> None:
    operation = model.operations.get(operation_name)
    if operation is None or not operation.supported:
        return
    supported_fields = {
        field
        for ref in operation.refs
        for field in ((ref.provider_field,) + ref.roles)
        if field
    }
    for field in IMAGE_REFERENCE_REQUEST_FIELDS - supported_fields:
        if _has_request_value(req, field):
            raise CatalogOperationValidationError(f"{model.id}.{operation_name}.{field} is not supported")
    for ref in operation.refs:
        values = _operation_ref_values(req, ref)
        if ref.required and not values:
            roles = ", ".join(ref.roles)
            raise CatalogOperationValidationError(f"{model.id}.{operation_name}.{roles} is required")
        if ref.max_total is not None and len(values) > ref.max_total:
            roles = ", ".join(ref.roles)
            raise CatalogOperationValidationError(
                f"{model.id}.{operation_name}.{roles} supports at most {ref.max_total} reference"
            )
        for value in values:
            _validate_operation_ref_value(model, operation_name, ref, value)


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


def _has_request_value(req: Any, name: str) -> bool:
    if not isinstance(req, Mapping):
        fields_set = getattr(req, "model_fields_set", None)
        if fields_set is not None and name not in fields_set:
            return False
    value = _request_value(req, name)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def _operation_supported(model: ModelCatalogEntry, operation_name: str) -> bool:
    operation = model.operations.get(operation_name)
    return bool(operation and operation.supported)


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
        _validate_size(label, spec, model.size, value)
    elif spec.kind == "aspect_ratio":
        _validate_aspect_ratio(label, spec, value)
    else:
        raise CatalogOperationValidationError(f"{label} has unsupported param kind")


def _validate_numeric_bounds(label: str, spec: OperationParamSpec, value: int | float) -> None:
    if spec.min is not None and value < spec.min:
        raise CatalogOperationValidationError(f"{label} must be greater than or equal to {spec.min}")
    if spec.max is not None and value > spec.max:
        raise CatalogOperationValidationError(f"{label} must be less than or equal to {spec.max}")


def _validate_size(label: str, spec: OperationParamSpec, model_size: SizeSpec, value: Any) -> None:
    if not isinstance(value, str):
        raise CatalogOperationValidationError(f"{label} must be a supported size string")
    allowed = {preset.value for preset in spec.presets}
    if value in allowed and SIZE_VALUE_RE.fullmatch(value) is None:
        return
    match = SIZE_VALUE_RE.fullmatch(value)
    if match is None:
        raise CatalogOperationValidationError(f"{label} must be a supported size string")
    if spec.mode == "preset":
        if value not in allowed:
            raise CatalogOperationValidationError(f"{label} has unsupported preset")
        return

    width, height = (int(part) for part in match.groups())
    _validate_size_bound(label, "width", width, model_size.min_width, model_size.max_width)
    _validate_size_bound(label, "height", height, model_size.min_height, model_size.max_height)
    pixels = width * height
    _validate_size_bound(label, "pixel count", pixels, model_size.min_pixels, model_size.max_pixels)
    if model_size.multiple_of is not None and (
        width % model_size.multiple_of or height % model_size.multiple_of
    ):
        raise CatalogOperationValidationError(
            f"{label} width and height must be multiples of {model_size.multiple_of}"
        )


def _validate_aspect_ratio(label: str, spec: OperationParamSpec, value: Any) -> None:
    if not isinstance(value, str):
        raise CatalogOperationValidationError(f"{label} must be a WIDTH:HEIGHT string")
    allowed = {preset.value for preset in spec.presets}
    if value not in allowed:
        raise CatalogOperationValidationError(f"{label} has unsupported preset")


def _validate_size_bound(
    label: str,
    name: str,
    value: int,
    minimum: int | None,
    maximum: int | None,
) -> None:
    if minimum is not None and value < minimum:
        raise CatalogOperationValidationError(f"{label} {name} must be greater than or equal to {minimum}")
    if maximum is not None and value > maximum:
        raise CatalogOperationValidationError(f"{label} {name} must be less than or equal to {maximum}")


def _operation_ref_values(req: Any, ref: OperationRefSpec) -> list[Any]:
    fields: list[str] = []
    if ref.provider_field:
        fields.append(ref.provider_field)
    fields.extend(field for field in ref.roles if field not in fields)
    values: list[Any] = []
    for field in fields:
        value = _request_value(req, field)
        if value is None:
            continue
        if isinstance(value, list):
            values.extend(item for item in value if _ref_value_present(item))
        elif _ref_value_present(value):
            values.append(value)
    return values


def _ref_value_present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def _validate_operation_ref_value(
    model: ModelCatalogEntry,
    operation_name: str,
    ref: OperationRefSpec,
    value: Any,
) -> None:
    label = f"{model.id}.{operation_name}.{','.join(ref.roles)}"
    if not {"url", "data_url"}.intersection(ref.formats):
        raise CatalogOperationValidationError(f"{label} has unsupported reference format")
    if not isinstance(value, str):
        raise CatalogOperationValidationError(f"{label} must be an image reference string")
    text = value.strip()
    if ref.provider_format == "url":
        if _safe_remote_reference_url(text):
            return
        raise CatalogOperationValidationError(
            f"{label} provider requires a public http(s) reference URL"
        )
    if (
        (ref.provider_format == "data_url" and _safe_gateway_reference_path(text))
        or ("url" in ref.formats and _safe_remote_reference_url(text))
        or ("data_url" in ref.formats and is_safe_image_data_url(text))
    ):
        return
    raise CatalogOperationValidationError(
        f"{label} must be a supported public URL, image data URL, or gateway asset path"
    )


def _safe_gateway_reference_path(value: str) -> bool:
    parsed = urlparse(value)
    return (
        not parsed.scheme
        and not parsed.netloc
        and not parsed.query
        and not parsed.fragment
        and parsed.path.startswith(("/uploads/", "/generated/"))
    )


def _safe_remote_reference_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and not parsed.query
        and not parsed.fragment
    )
