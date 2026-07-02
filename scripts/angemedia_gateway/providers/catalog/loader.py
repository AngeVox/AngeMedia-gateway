"""Load and validate the local static provider catalog."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .common import _reject_unknown_keys
from .errors import CatalogValidationError
from .parsers import _parse_models, _parse_providers
from .schema import ProviderCatalog


CATALOG_DIR = Path(__file__).resolve().parent
PROVIDERS_FILE = "providers.yaml"
MODELS_FILE = "models.yaml"

PROVIDERS_TOP_KEYS = {"providers"}
MODELS_TOP_KEYS = {"models"}


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
