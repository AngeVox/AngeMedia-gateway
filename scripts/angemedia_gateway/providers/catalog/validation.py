"""Catalog-driven request validation for model operations."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlparse

from ...reference_images import is_safe_image_data_url
from ...security import validate_provider_reference_url
from .loader import load_provider_catalog
from .schema import ModelCatalogEntry, OperationParamSpec, OperationRefSpec, ProviderCatalog, SizeSpec


_LAYER_DECOMPOSITION_PARAMS = {"layer_decomposition", "layer_decomposition_options"}
IMAGE_OPERATION_PARAM_NAMES = frozenset({
    "prompt", "size", "aspect_ratio", "quality", "complexity", "output_format", "output_compression", "background", "watermark",
    "negative_prompt", "seed", "steps", "guidance", "frame_count", "frame_duration_ms",
    "sequential_image_generation", "sequential_image_generation_options", "stream",
} | _LAYER_DECOMPOSITION_PARAMS)
_EDIT_AUXILIARY_FIELDS = frozenset({"mask", "mask_image", "markup"})
IMAGE_REFERENCE_REQUEST_FIELDS = frozenset({
    "image", "images", "input_image", "input_images", "init_image",
    "reference_image", "reference_images", "control_image",
}) | _EDIT_AUXILIARY_FIELDS
SIZE_VALUE_RE = re.compile(r"^([1-9]\d{1,3})x([1-9]\d{1,3})$")


class CatalogOperationValidationError(ValueError):
    """Raised when a request violates catalog operation capability bounds."""


def validate_image_operation_request(
    req: Any,
    targets: Iterable[Any],
    *,
    catalog: ProviderCatalog | None = None,
) -> None:
    """按已解析型号校验操作；SiliconFlow和ModelScope另守各自公共请求边界。

    其他渠道的未知型号继续沿用旧合同，不据此推断新的操作能力。
    """

    _validate_animation_options(req)
    _validate_svg_options(req)
    loaded_catalog = catalog or load_provider_catalog()
    extra_body = _request_value(req, "extra_body")
    if isinstance(extra_body, Mapping) and "markup" in extra_body:
        raise CatalogOperationValidationError("markup must be provided as a dedicated field")
    has_markup = _has_request_value(req, "markup")
    if has_markup:
        if any(_has_request_value(req, field) for field in ("mask", "mask_image")):
            raise CatalogOperationValidationError("markup cannot be used with a mask")
        if _request_value(req, "output_format") == "gif":
            raise CatalogOperationValidationError("markup requires a static image output")
    has_nested_layers = isinstance(extra_body, Mapping) and not _LAYER_DECOMPOSITION_PARAMS.isdisjoint(extra_body)
    for target in targets:
        model = _catalog_model_for_target(loaded_catalog, target)
        provider = _target_value(target, "provider")
        if provider == "modelscope":
            _validate_modelscope_envelope(req, model)
        if provider == "siliconflow":
            _validate_siliconflow_envelope(req, model)
        if provider == "bytedance" and model is not None and has_nested_layers:
            raise CatalogOperationValidationError(f"{model.id} does not support layer decomposition")
        if model is None or not model.operations:
            for name in ("output_compression", "background", "complexity", "frame_count", "frame_duration_ms", "markup"):
                if _has_request_value(req, name):
                    raise CatalogOperationValidationError(f"{name} requires a declared model operation")
            if _request_value(req, "output_format") in {"webp", "gif", "svg"}:
                raise CatalogOperationValidationError("selected output format requires a declared model operation")
        if model is None:
            continue
        operation_name = image_operation_for_request(req, model)
        validate_operation_params(req, model, operation_name)
        validate_operation_refs(req, model, operation_name)


def _validate_svg_options(req: Any) -> None:
    if _request_value(req, "output_format") != "svg":
        return
    if _request_value(req, "operation") == "edit" or any(
        _has_request_value(req, field) for field in IMAGE_REFERENCE_REQUEST_FIELDS | {"image_url"}
    ):
        raise CatalogOperationValidationError("SVG output is available for text-to-image generation only")
    if any(_has_request_value(req, field) for field in (
        "output_compression", "stream", "partial_images", "prompt_rewriter", "svg", "extra_body",
    )):
        raise CatalogOperationValidationError("SVG output does not support compression, streaming or advanced author controls")


def _validate_animation_options(req: Any) -> None:
    if _request_value(req, "output_format") != "gif":
        if any(_has_request_value(req, key) for key in ("frame_count", "frame_duration_ms")):
            raise CatalogOperationValidationError("GIF frame settings require GIF output")
        return
    unsupported = ("mask", "mask_image", "output_compression", "stream", "partial_images")
    if any(_has_request_value(req, key) for key in unsupported):
        raise CatalogOperationValidationError("GIF output does not support masks, compression or streaming")


def _validate_modelscope_envelope(req: Any, model: ModelCatalogEntry | None) -> None:
    """按官方公共信封校验；默认链由规划服务逐条保留兼容候选。"""
    for name, maximum in (('prompt', 1999), ('negative_prompt', 3999)):
        value = _request_value(req, name)
        if value is not None and (not isinstance(value, str) or len(value) > maximum):
            raise CatalogOperationValidationError(f'ModelScope {name} exceeds its documented character limit')
    for name, low, high in (('seed', 0, 2147483647), ('steps', 1, 100)):
        value = _request_value(req, name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high):
            raise CatalogOperationValidationError(f'ModelScope {name} is outside its documented range')
    for name in ('safe', 'user', 'provider_model'):
        if _has_request_value(req, name):
            raise CatalogOperationValidationError(f'ModelScope does not declare {name}')
    extras = req if isinstance(req, Mapping) else (getattr(req, 'model_extra', None) or {})
    allowed_extra = {'images', 'image_url'} if model is not None and 'image_edit' in model.operations else set()
    if isinstance(req, Mapping):
        known = IMAGE_OPERATION_PARAM_NAMES | IMAGE_REFERENCE_REQUEST_FIELDS | {'model', 'n', 'operation', 'response_format', 'safe', 'user', 'provider_model'}
        extras = set(extras) - known
    if set(extras) - allowed_extra:
        raise CatalogOperationValidationError('ModelScope received undeclared extra fields')
    if model is not None:
        return
    allowed = {'prompt', 'size', 'negative_prompt', 'seed', 'steps'}
    for name in (IMAGE_OPERATION_PARAM_NAMES - allowed) | IMAGE_REFERENCE_REQUEST_FIELDS | {'image_url'}:
        if _has_request_value(req, name):
            raise CatalogOperationValidationError(f'unverified ModelScope model requires an operation contract for {name}')
    if _request_value(req, 'operation') not in {None, 'auto', 'generate'}:
        raise CatalogOperationValidationError('unverified ModelScope model only accepts text generation')
    size = _request_value(req, 'size')
    if not isinstance(size, str) or SIZE_VALUE_RE.fullmatch(size) is None:
        raise CatalogOperationValidationError('unverified ModelScope model requires a WIDTHxHEIGHT size')


def _validate_siliconflow_envelope(req: Any, model: ModelCatalogEntry | None) -> None:
    """已撤销字段全站拒绝；未收录型号只承诺通用文字请求，不推断模型专用参数。"""
    extras = req if isinstance(req, Mapping) else (getattr(req, "model_extra", None) or {})
    extra_body = _request_value(req, "extra_body")
    if "batch_size" in extras or isinstance(extra_body, Mapping) and "batch_size" in extra_body:
        raise CatalogOperationValidationError("SiliconFlow batch_size is no longer supported")
    if model is not None and model.operations:
        return
    allowed_params = {"prompt", "size", "negative_prompt", "seed", "steps"}
    for name in (IMAGE_OPERATION_PARAM_NAMES - allowed_params) | IMAGE_REFERENCE_REQUEST_FIELDS | {"safe", "user", "provider_model"}:
        if _has_request_value(req, name):
            raise CatalogOperationValidationError(f"unverified SiliconFlow model requires an operation contract for {name}")
    if _request_value(req, "operation") not in {None, "auto", "generate"}:
        raise CatalogOperationValidationError("unverified SiliconFlow model only accepts text generation")
    size = _request_value(req, "size")
    if not isinstance(size, str) or SIZE_VALUE_RE.fullmatch(size) is None:
        raise CatalogOperationValidationError("unverified SiliconFlow model requires a WIDTHxHEIGHT size")
    if isinstance(req, Mapping):
        known_fields = IMAGE_OPERATION_PARAM_NAMES | IMAGE_REFERENCE_REQUEST_FIELDS | {"model", "n", "response_format", "operation", "safe", "user", "provider_model"}
        extras = set(extras) - known_fields
    if extras:
        raise CatalogOperationValidationError("unverified SiliconFlow model received undeclared extra fields")


def image_operation_for_request(req: Any, model: ModelCatalogEntry) -> str:
    requested = str(_request_value(req, "operation") or "auto").strip().lower()
    has_edit_auxiliary = any(_has_request_value(req, field) for field in _EDIT_AUXILIARY_FIELDS)
    has_references = any(
        _has_request_value(req, field)
        for field in IMAGE_REFERENCE_REQUEST_FIELDS - _EDIT_AUXILIARY_FIELDS
    )
    # 原生引用别名也必须参与自动操作选择，不能绕过目录或被误当成文生图。
    has_references = has_references or any(
        ref.provider_field and _has_request_value(req, ref.provider_field)
        for operation in model.operations.values()
        for ref in operation.refs
        if not _EDIT_AUXILIARY_FIELDS.intersection(ref.roles)
    )

    if requested == "generate":
        if not _operation_supported(model, "text_to_image") and model.operations:
            raise CatalogOperationValidationError(f"{model.id} does not support text-to-image generation")
        return "text_to_image"
    if requested == "edit":
        if _operation_supported(model, "image_edit"):
            return "image_edit"
        raise CatalogOperationValidationError(f"{model.id} does not support image editing")
    if requested != "auto":
        raise CatalogOperationValidationError(f"{model.id} has unsupported image operation")

    if has_edit_auxiliary:
        if _operation_supported(model, "image_edit"):
            return "image_edit"
        raise CatalogOperationValidationError(f"{model.id} does not support masks or markup")
    if has_references:
        if _operation_supported(model, "image_to_image"):
            return "image_to_image"
        if _operation_supported(model, "image_edit"):
            return "image_edit"
        if model.operations:
            raise CatalogOperationValidationError(f"{model.id} does not support image references")
    if not _operation_supported(model, "text_to_image") and model.operations:
        raise CatalogOperationValidationError(f"{model.id} does not support text-to-image generation")
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
    # 输出选项只在目录声明支持时联动，不能将官方站能力套用到未知兼容型号。
    output_format = _request_value(req, "output_format")
    format_spec = operation.params.get("output_format")
    effective_format = output_format or (format_spec.default if format_spec else None)
    if "background" in operation.params and _request_value(req, "background") == "transparent" and effective_format not in {"png", "webp"}:
        raise CatalogOperationValidationError(f"{model.id}.{operation_name}.transparent background requires png or webp")
    if "output_compression" in operation.params and _request_value(req, "output_compression") is not None and effective_format not in {"jpeg", "webp"}:
        raise CatalogOperationValidationError(f"{model.id}.{operation_name}.output_compression requires jpeg or webp")


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
    if model_size.max_aspect_ratio is not None:
        ratio = max(width, height) / min(width, height)
        if ratio > model_size.max_aspect_ratio:
            raise CatalogOperationValidationError(
                f"{label} aspect ratio must not exceed {model_size.max_aspect_ratio}:1"
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
        if (
            _safe_remote_reference_url(text)
            or _safe_gateway_reference_path(text)
            or is_safe_image_data_url(text)
        ):
            return
        raise CatalogOperationValidationError(
            f"{label} requires a public URL or a relay-capable gateway/data reference"
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
    if parsed.query or parsed.fragment:
        return False
    try:
        validate_provider_reference_url(value)
    except ValueError:
        return False
    return True
