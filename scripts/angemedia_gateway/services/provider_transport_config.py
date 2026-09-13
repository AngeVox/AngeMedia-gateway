"""Admin-facing Provider transport configuration service."""
from __future__ import annotations

from typing import Any

from ..providers.catalog.loader import CatalogValidationError, load_provider_catalog
from ..providers.transport_config import resolve_saved_provider_transport
from ..providers.transport_policy import DIRECT, EXPLICIT_PROXY, validate_explicit_proxy_url
from ..repositories.settings import get_custom_provider
from ..repositories.provider_transport_config import (
    get_provider_transport_config,
    update_provider_transport_config,
)


class ProviderTransportConfigError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ProviderTransportConfigService:
    def get_config(self, provider_id: str | None = None) -> dict[str, Any]:
        self._require_provider(provider_id)
        try:
            return self._summary(provider_id)
        except ValueError as exc:
            raise ProviderTransportConfigError(400, str(exc)) from exc

    def update_config(self, provider_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_provider(provider_id)
        try:
            current = get_provider_transport_config(provider_id)
        except ValueError as exc:
            raise ProviderTransportConfigError(400, str(exc)) from exc

        mode = current.get("transport_mode")
        proxy_url = current.get("proxy_url")

        if "transport_mode" in payload:
            raw_mode = payload.get("transport_mode")
            mode = str(raw_mode or "").strip().lower() or None
            if mode not in {None, DIRECT, EXPLICIT_PROXY}:
                raise ProviderTransportConfigError(400, "Unsupported Provider transport mode.")

        if "proxy_url" in payload:
            raw_proxy = str(payload.get("proxy_url") or "").strip()
            if raw_proxy:
                try:
                    proxy_url = validate_explicit_proxy_url(raw_proxy)
                except ValueError as exc:
                    raise ProviderTransportConfigError(400, str(exc)) from exc
            else:
                proxy_url = None

        if mode == EXPLICIT_PROXY and not proxy_url:
            raise ProviderTransportConfigError(400, "explicit_proxy mode requires proxy_url.")

        updates: dict[str, Any] = {}
        if "transport_mode" in payload:
            updates["transport_mode"] = mode
        if "proxy_url" in payload:
            updates["proxy_url"] = proxy_url
        if updates:
            update_provider_transport_config(provider_id, **updates)
        return self._summary(provider_id)


    def clear_config(self, provider_id: str) -> None:
        """Remove per-Provider transport state after a custom Provider is deleted."""
        try:
            update_provider_transport_config(provider_id, transport_mode=None, proxy_url=None)
        except ValueError as exc:
            raise ProviderTransportConfigError(400, str(exc)) from exc

    @staticmethod
    def _require_provider(provider_id: str | None) -> None:
        if provider_id is None:
            return
        value = str(provider_id or "").strip().lower()
        try:
            catalog = load_provider_catalog()
        except CatalogValidationError as exc:
            raise ProviderTransportConfigError(500, "Provider catalog is invalid.") from exc
        if value in catalog.providers_by_id:
            return
        try:
            custom = get_custom_provider(value, include_secret=False)
        except Exception as exc:
            status_code = int(getattr(exc, "status_code", 400) or 400)
            raise ProviderTransportConfigError(status_code, str(getattr(exc, "detail", exc))) from exc
        if custom is None:
            raise ProviderTransportConfigError(404, "Provider not found.")

    @staticmethod
    def _summary(provider_id: str | None) -> dict[str, Any]:
        stored = get_provider_transport_config(provider_id)
        decision = resolve_saved_provider_transport(provider_id)
        return {
            "scope": "global" if provider_id is None else "provider",
            "provider_id": provider_id,
            "transport_mode": stored.get("transport_mode"),
            "proxy_configured": bool(stored.get("proxy_url")),
            "effective_mode": decision.mode,
            "effective_source": decision.source,
            "effective_proxy_configured": bool(decision.proxy_url),
        }
