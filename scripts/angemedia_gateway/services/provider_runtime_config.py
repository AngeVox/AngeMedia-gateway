"""内置渠道运行配置与独立连接证据的管理入口。"""
from __future__ import annotations

from typing import Any

from .. import config as C
from ..providers.catalog.loader import CatalogValidationError, load_provider_catalog
from ..providers.catalog.schema import ProviderCatalog, ProviderCatalogEntry
from ..providers.runtime_config import MODELSCOPE_API_BASE_URL, provider_key_preview, resolve_provider_runtime_config
from ..repositories.provider_runtime_config import (
    clear_provider_runtime_api_key,
    update_provider_runtime_config,
)
from ..repositories.settings import builtin_provider_enabled
from .provider_connection_evidence import read_connection_evidence, test_and_record_connection
from ..providers.endpoint_policy import validate_provider_base_url
from ..video_models import DEFAULT_AGNES_VIDEO_MODEL
from ..providers.catalog.policy import policy_api_response
from ..providers.catalog.api import catalog_api_response


class ProviderRuntimeConfigError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ProviderRuntimeConfigService:
    def catalog_response(self, catalog: ProviderCatalog) -> dict[str, Any]:
        """静态能力与当前本地配置分开投影，不把密钥存在当作账户生成验证。"""
        response = catalog_api_response(catalog)
        for item in response['providers']:
            entry = catalog.providers_by_id[item['id']]
            builtin = self._source(entry) == 'builtin'
            runtime = resolve_provider_runtime_config(entry.id) if builtin else None
            item['runtime'] = {
                'enabled': builtin and builtin_provider_enabled(entry.id),
                'api_key_configured': bool(runtime and runtime.api_key),
            }
            # 官方账户目录只用于工作台选型，配置的自定义地址仍走原目录。
            if entry.id == 'modelscope' and runtime and runtime.base_url == MODELSCOPE_API_BASE_URL:
                evidence = read_connection_evidence(entry.id)
                item['runtime']['model_directory'] = {
                    key: evidence[key] for key in (
                        'status', 'evidence_state', 'server_time', 'expires_at',
                        'model_list_validated', 'project_model_visibility',
                    ) if key in evidence
                }
        return response

    def list_configs(self) -> list[dict[str, Any]]:
        catalog = self._catalog()
        return [
            self._summary(entry, catalog)
            for entry in catalog.providers
            if self._source(entry) == "builtin"
        ]

    def update_config(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        catalog = self._catalog()
        entry = self._editable_entry(provider_id, catalog)
        updates: dict[str, Any] = {}
        if "enabled" in payload:
            updates["enabled"] = bool(payload["enabled"])
        if "api_key" in payload:
            api_key = str(payload.get("api_key") or "").strip()
            if len(api_key) > 4096:
                raise ProviderRuntimeConfigError(400, "API Key is too long")
            if api_key:
                updates["api_key"] = api_key
        if "base_url_override" in payload:
            base_url = str(payload.get("base_url_override") or "").strip()
            if base_url:
                try:
                    base_url = validate_provider_base_url(base_url)
                except ValueError as exc:
                    raise ProviderRuntimeConfigError(400, str(exc)) from exc
            updates["base_url_override"] = base_url or None
        if updates:
            update_provider_runtime_config(provider_id, **updates)
        return self._summary(entry, catalog)

    def clear_key(self, provider_id: str) -> dict[str, Any]:
        catalog = self._catalog()
        entry = self._editable_entry(provider_id, catalog)
        clear_provider_runtime_api_key(provider_id)
        return self._summary(entry, catalog)

    async def test_connection(self, provider_id: str) -> dict[str, Any]:
        catalog = self._catalog()
        self._editable_entry(provider_id, catalog)
        return await test_and_record_connection(provider_id)

    def _catalog(self) -> ProviderCatalog:
        try:
            return load_provider_catalog()
        except CatalogValidationError as exc:
            raise ProviderRuntimeConfigError(500, "Provider catalog is invalid") from exc

    def _editable_entry(self, provider_id: str, catalog: ProviderCatalog) -> ProviderCatalogEntry:
        entry = catalog.providers_by_id.get(provider_id)
        if entry is None:
            raise ProviderRuntimeConfigError(404, "Provider does not exist")
        if self._source(entry) != "builtin":
            raise ProviderRuntimeConfigError(409, "Catalog and reserved providers are read-only")
        return entry

    @staticmethod
    def _source(entry: ProviderCatalogEntry) -> str:
        if entry.status == "reserved":
            return "reserved"
        if entry.ui_group.startswith("builtin"):
            return "builtin"
        return "catalog"

    def _summary(self, entry: ProviderCatalogEntry, catalog: ProviderCatalog) -> dict[str, Any]:
        runtime = resolve_provider_runtime_config(entry.id)
        default_model = self._default_model(entry.id, catalog)
        return {
            "provider_id": entry.id,
            "display_name": entry.display_name,
            "provider_type": entry.ui_group,
            "media_types": list(entry.media_types),
            "enabled": builtin_provider_enabled(entry.id),
            "api_key_configured": bool(runtime.api_key),
            "api_key_preview": provider_key_preview(runtime.api_key),
            "base_url_override": runtime.base_url_override,
            "default_model": default_model,
            "default_model_policy": policy_api_response(next((model.policy for model in catalog.models if model.provider == entry.id and model.provider_model == default_model), None)),
            "default_model_override": runtime.default_model_override,
            "source": "builtin",
            "updated_at": runtime.updated_at,
            "connection_evidence": read_connection_evidence(entry.id),
        }

    @staticmethod
    def _default_model(provider_id: str, catalog: ProviderCatalog) -> str | None:
        configured = {
            "pollinations": C.POLLINATIONS_DEFAULT_MODEL,
            "openai_image": C.OPENAI_IMAGE_MODEL,
            "agnes_image": C.AGNES_IMAGE_MODEL,
            "agnes_video": DEFAULT_AGNES_VIDEO_MODEL,
        }.get(provider_id)
        if configured:
            return configured
        models = [model for model in catalog.models if model.provider == provider_id and model.status != "reserved"]
        models.sort(key=lambda item: (item.default_chain_order is None, item.default_chain_order or 10_000, item.id))
        return models[0].provider_model if models else None
