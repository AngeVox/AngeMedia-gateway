"""Static provider catalog schema.

The catalog is data only: it describes built-in providers and models, but it
does not import adapter code or change runtime routing by itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


VALID_PROVIDER_STATUSES = {"release", "experimental", "reserved"}
VALID_MODEL_STATUSES = {"release", "experimental", "reserved"}
VALID_MEDIA_TYPES = {"image", "video"}
VALID_CAPABILITIES = {
    "text_to_image",
    "image_to_image",
    "text_to_video",
    "image_to_video",
}
VALID_PARAM_KINDS = {"string", "int", "float", "bool", "enum", "seed"}
VALID_SIZE_MODES = {"preset", "freeform"}
VALID_OPERATIONS = {
    "text_to_image",
    "image_to_image",
    "image_edit",
    "text_to_video",
    "image_to_video",
}
VALID_OPERATION_PARAM_KINDS = VALID_PARAM_KINDS | {"size"}
VALID_OPERATION_EVIDENCE = {"official_api", "official_model_page", "third_party", "unknown"}


@dataclass(frozen=True)
class ParamSpec:
    kind: str
    default: Any | None
    min: int | float | None
    max: int | float | None
    enum_values: tuple[Any, ...]


@dataclass(frozen=True)
class SizeSpec:
    mode: str
    presets: tuple[str, ...]
    min_width: int | None
    max_width: int | None
    min_height: int | None
    max_height: int | None
    min_pixels: int | None
    max_pixels: int | None
    multiple_of: int | None


@dataclass(frozen=True)
class RefInputSpec:
    roles: tuple[str, ...]
    max_total: int | None
    formats: tuple[str, ...]
    required: bool


@dataclass(frozen=True)
class OperationSizePreset:
    value: str
    label: str | None


@dataclass(frozen=True)
class OperationParamSpec:
    kind: str
    required: bool
    provider_field: str | None
    evidence: str
    default: Any | None
    min: int | float | None
    max: int | float | None
    enum_values: tuple[Any, ...]
    mode: str | None
    presets: tuple[OperationSizePreset, ...]


@dataclass(frozen=True)
class OperationRefSpec:
    roles: tuple[str, ...]
    provider_field: str | None
    max_total: int | None
    formats: tuple[str, ...]
    required: bool


@dataclass(frozen=True)
class OperationSpec:
    supported: bool
    params: dict[str, OperationParamSpec]
    refs: tuple[OperationRefSpec, ...]


@dataclass(frozen=True)
class ProviderCatalogEntry:
    id: str
    display_name: str
    media_types: tuple[str, ...]
    status: str
    enabled_default: bool
    config_enabled_key: str | None
    requires_key: bool
    credential_keys: tuple[str, ...]
    adapter_id: str
    ui_group: str
    notes: str


@dataclass(frozen=True)
class ModelCatalogEntry:
    id: str
    provider: str
    provider_model: str
    media_type: str
    display_name: str
    aliases: tuple[str, ...]
    status: str
    selectable: bool
    default_chain_order: int | None
    capabilities: dict[str, bool]
    params: dict[str, Any]
    param_specs: dict[str, ParamSpec]
    size_presets: tuple[str, ...]
    size: SizeSpec
    ref_inputs: dict[str, Any]
    ref_input_spec: RefInputSpec
    operations: dict[str, OperationSpec]
    extra_allowlist: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class ProviderCatalog:
    providers: tuple[ProviderCatalogEntry, ...]
    models: tuple[ModelCatalogEntry, ...]

    @property
    def providers_by_id(self) -> dict[str, ProviderCatalogEntry]:
        return {item.id: item for item in self.providers}

    @property
    def models_by_id(self) -> dict[str, ModelCatalogEntry]:
        return {item.id: item for item in self.models}

    def default_image_chain(self) -> list[ModelCatalogEntry]:
        return sorted(
            (
                model
                for model in self.models
                if model.media_type == "image" and model.default_chain_order is not None
            ),
            key=lambda item: item.default_chain_order or 0,
        )
