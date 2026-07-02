"""Provider admin detail/edit/test orchestration."""
from __future__ import annotations

from typing import Any

from ..providers.catalog.loader import CatalogValidationError, load_provider_catalog
from ..providers.errors import ProviderError
from ..repositories.settings import (
    BUILTIN_PROVIDER_CONFIG_KEYS,
    get_custom_provider,
    upsert_custom_provider,
    update_custom_provider_details,
    update_custom_provider_test,
)
from .admin_service import AdminService
from .provider_test import fetch_openai_compatible_model_ids, provider_error_status, provider_test_message
from .provider_url_policy import validate_provider_base_url, validate_provider_probe_url


EDITABLE_PROVIDER_FIELDS = {"name", "display_name", "base_url", "default_model", "enabled", "api_key", "notes"}
DISALLOWED_PROVIDER_EDIT_FIELDS = {"status_url", "quota_url", "sort_order", "last_error"}


class ProviderAdminError(Exception):
    def __init__(self, status_code: int, detail: str | dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class ProviderAdminService:
    def __init__(self, admin_service: AdminService) -> None:
        self.admin_service = admin_service

    def create_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        if data.get("base_url"):
            try:
                data["base_url"] = validate_provider_base_url(data["base_url"])
            except ValueError as exc:
                raise ProviderAdminError(400, str(exc)) from exc
        for key in ("status_url", "quota_url"):
            if data.get(key):
                try:
                    data[key] = validate_provider_probe_url(data[key])
                except ValueError as exc:
                    raise ProviderAdminError(400, str(exc)) from exc
        try:
            created = upsert_custom_provider(data)
        except Exception as exc:
            status_code = int(getattr(exc, "status_code", 400) or 400)
            detail = getattr(exc, "detail", str(exc))
            raise ProviderAdminError(status_code, detail) from exc
        return _custom_provider_summary(created)

    def provider_detail(self, provider_id: str) -> dict[str, Any]:
        custom = get_custom_provider(provider_id, include_secret=True)
        if custom is not None:
            return _custom_provider_detail(custom)

        builtin = self._builtin_detail(provider_id)
        if builtin is not None:
            return builtin

        if self._catalog_provider_exists(provider_id):
            raise _read_only_error()
        raise ProviderAdminError(404, "Provider not found")

    def edit_provider(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if provider_id in BUILTIN_PROVIDER_CONFIG_KEYS or self._catalog_provider_exists(provider_id):
            raise _read_only_error()
        existing = get_custom_provider(provider_id, include_secret=True)
        if existing is None:
            raise ProviderAdminError(404, "Provider not found")

        unsupported = (set(payload) - EDITABLE_PROVIDER_FIELDS) | (set(payload) & DISALLOWED_PROVIDER_EDIT_FIELDS)
        if unsupported:
            raise ProviderAdminError(
                400,
                {
                    "code": "unsupported_provider_edit_fields",
                    "message": "Unsupported provider edit fields.",
                    "fields": sorted(unsupported),
                },
            )

        data = dict(payload)
        if "display_name" in data and "name" not in data:
            data["name"] = data.pop("display_name")
        if "base_url" in data:
            try:
                data["base_url"] = validate_provider_base_url(data["base_url"])
            except ValueError as exc:
                raise ProviderAdminError(
                    400,
                    {
                        "code": "invalid_base_url",
                        "message": "Provider base URL is invalid.",
                    },
                ) from exc

        try:
            updated = update_custom_provider_details(provider_id, data)
        except ValueError as exc:
            raise ProviderAdminError(400, {"code": "invalid_provider_edit", "message": str(exc)}) from exc
        return _custom_provider_summary(updated)

    async def test_provider(self, provider_id: str) -> dict[str, Any]:
        if provider_id in BUILTIN_PROVIDER_CONFIG_KEYS or self._catalog_provider_exists(provider_id):
            raise _test_not_supported_error()

        provider = get_custom_provider(provider_id, include_secret=True)
        if provider is None:
            raise ProviderAdminError(404, "Provider not found")
        if provider.get("provider_type") != "openai_image":
            raise _test_not_supported_error()

        try:
            base_url = validate_provider_base_url(str(provider.get("base_url") or ""))
        except ValueError as exc:
            updated = update_custom_provider_test(provider_id, "invalid_base_url", 0, provider_test_message("invalid_base_url"))
            return _test_response(
                provider,
                ok=False,
                status="invalid_base_url",
                elapsed_ms=0,
                models=[],
                model_found=False,
                data=updated,
            )

        model = str(provider.get("default_model") or "")
        try:
            models, elapsed_ms = await fetch_openai_compatible_model_ids(base_url, str(provider.get("api_key") or ""))
            status = "ok" if (not models or model in models) else "model_not_found"
            model_found = status == "ok"
        except ProviderError as exc:
            status = provider_error_status(exc)
            elapsed_ms = 0
            models = []
            model_found = False
            updated = update_custom_provider_test(provider_id, status, elapsed_ms, provider_test_message(status))
            return _test_response(
                provider,
                ok=False,
                status=status,
                elapsed_ms=elapsed_ms,
                models=models,
                model_found=model_found,
                data=updated,
            )
        except Exception:
            status = "upstream_unavailable"
            elapsed_ms = 0
            models = []
            model_found = False
            updated = update_custom_provider_test(provider_id, status, elapsed_ms, provider_test_message(status))
            return _test_response(
                provider,
                ok=False,
                status=status,
                elapsed_ms=elapsed_ms,
                models=models,
                model_found=model_found,
                data=updated,
            )

        updated = update_custom_provider_test(
            provider_id,
            status,
            elapsed_ms,
            "" if status == "ok" else provider_test_message(status),
        )
        return _test_response(
            provider,
            ok=status == "ok",
            status=status,
            elapsed_ms=elapsed_ms,
            models=models,
            model_found=model_found,
            data=updated,
        )

    def _builtin_detail(self, provider_id: str) -> dict[str, Any] | None:
        row = next((item for item in self.admin_service.builtin_provider_rows() if item["id"] == provider_id), None)
        if row is None:
            return None
        return {
            "id": row.get("id"),
            "name": row.get("name"),
            "provider_type": row.get("provider_type"),
            "source": "builtin",
            "enabled": bool(row.get("enabled")),
            "configured": bool(row.get("configured")),
            "ready": bool(row.get("ready")),
            "default_model": row.get("default_model"),
            "category": row.get("category"),
            "editable": False,
            "read_only": True,
            "api_key_configured": bool(row.get("configured")),
        }

    def _catalog_provider_exists(self, provider_id: str) -> bool:
        try:
            catalog = load_provider_catalog()
        except CatalogValidationError:
            return False
        return provider_id in catalog.providers_by_id


def _custom_provider_detail(provider: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": provider.get("id"),
        "name": provider.get("name"),
        "provider_type": provider.get("provider_type"),
        "source": "custom",
        "editable": True,
        "read_only": False,
        "base_url": provider.get("base_url"),
        "default_model": provider.get("default_model"),
        "enabled": bool(provider.get("enabled")),
        "notes": provider.get("notes") or "",
        "api_key_configured": bool(provider.get("api_key")),
        "last_test_at": provider.get("last_test_at"),
        "last_test_status": provider.get("last_test_status"),
        "last_response_ms": provider.get("last_response_ms"),
        "created_at": provider.get("created_at"),
        "updated_at": provider.get("updated_at"),
    }


def _custom_provider_summary(provider: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": provider.get("id"),
        "name": provider.get("name"),
        "provider_type": provider.get("provider_type"),
        "enabled": bool(provider.get("enabled")),
        "api_key_configured": bool(provider.get("api_key")),
        "default_model": provider.get("default_model"),
        "sort_order": provider.get("sort_order"),
        "last_test_status": provider.get("last_test_status"),
        "last_response_ms": provider.get("last_response_ms"),
        "last_test_at": provider.get("last_test_at"),
        "created_at": provider.get("created_at"),
        "updated_at": provider.get("updated_at"),
    }


def _test_response(
    provider: dict[str, Any],
    *,
    ok: bool,
    status: str,
    elapsed_ms: int,
    models: list[str],
    model_found: bool,
    data: dict[str, Any],
) -> dict[str, Any]:
    model = str(provider.get("default_model") or "")
    return {
        "ok": ok,
        "status": status,
        "provider_id": provider.get("id"),
        "provider_type": provider.get("provider_type"),
        "model": model,
        "model_found": model_found,
        "elapsed_ms": int(elapsed_ms or 0),
        "message": provider_test_message(status),
        "models": models,
        "data": _custom_provider_summary(data),
    }


def _read_only_error() -> ProviderAdminError:
    return ProviderAdminError(
        409,
        {
            "code": "provider_read_only",
            "message": "Only custom providers can be edited.",
        },
    )


def _test_not_supported_error() -> ProviderAdminError:
    return ProviderAdminError(
        409,
        {
            "code": "test_not_supported",
            "status": "test_not_supported",
            "message": provider_test_message("test_not_supported"),
        },
    )
