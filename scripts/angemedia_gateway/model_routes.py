"""从现有目录解析图片模型标识，不猜测未知型号所属渠道。"""
from __future__ import annotations

from functools import lru_cache

from .providers.base import RouteTarget
from .providers.catalog.loader import load_provider_catalog
from .providers.catalog.schema import ModelCatalogEntry, ProviderCatalog


class InvalidModelSelector(ValueError):
    """请求的模型标识无法唯一解析到指定媒体与渠道。"""


@lru_cache(maxsize=1)
def _catalog() -> ProviderCatalog:
    """静态目录只加载一次；运行开关仍由调用方实时判断。"""
    return load_provider_catalog()


def _selectors(model: ModelCatalogEntry) -> set[str]:
    return {value.lower() for value in (model.id, model.provider_model, *model.aliases)}


def catalog_image_aliases() -> dict[str, RouteTarget]:
    """只导出唯一的图片选择项；跨渠道重名项必须显式指定渠道。"""
    candidates: dict[str, set[tuple[str, str]]] = {}
    for model in _catalog().models:
        if model.media_type != "image":
            continue
        for selector in _selectors(model):
            candidates.setdefault(selector, set()).add((model.provider, model.provider_model))
    return {
        selector: RouteTarget(*next(iter(targets)))
        for selector, targets in candidates.items()
        if len(targets) == 1
    }


def catalog_default_image_chain() -> list[RouteTarget]:
    """沿用目录中明确配置的默认顺序，不自动纳入新增型号。"""
    return [RouteTarget(model.provider, model.provider_model) for model in _catalog().default_image_chain()]


def resolve_image_target(selector: str, aliases: dict[str, RouteTarget]) -> RouteTarget:
    """支持目录ID、别名、上游ID和provider::model形式的显式渠道范围。"""
    raw = selector.strip()
    if not raw or len(raw) > 512 or any(ord(char) < 32 for char in raw):
        raise InvalidModelSelector("模型标识为空或格式无效")
    provider, separator, model_name = raw.partition("::")
    if not separator:
        target = aliases.get(raw.lower())
        if target is not None:
            return target
        raise InvalidModelSelector("无法唯一识别图片模型；请使用目录ID、模型别名或 provider::上游模型ID")

    provider = provider.strip().lower()
    model_name = model_name.strip()
    entry = _catalog().providers_by_id.get(provider)
    if entry is None or "image" not in entry.media_types or not model_name or "::" in model_name:
        raise InvalidModelSelector("图片渠道范围无效；请使用 provider::上游模型ID")
    candidate = aliases.get(model_name.lower())
    if candidate is not None and candidate.provider == provider:
        return candidate
    matches = [model for model in _catalog().models if model_name.lower() in _selectors(model)]
    scoped = {(model.provider, model.provider_model) for model in matches if model.provider == provider and model.media_type == "image"}
    if len(scoped) == 1:
        return RouteTarget(*next(iter(scoped)))
    if matches:
        raise InvalidModelSelector("所选模型不属于指定图片渠道")
    # 目录外型号只允许在调用者明确指定渠道时透传，不推断能力或自动启用。
    return RouteTarget(provider, model_name)
