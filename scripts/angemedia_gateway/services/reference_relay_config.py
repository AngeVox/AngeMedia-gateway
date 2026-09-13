"""Admin-facing reference relay configuration."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ..providers.endpoint_policy import validate_provider_probe_url
from ..repositories.reference_relay_config import get_reference_relay_config, update_reference_relay_config

DISABLED = "disabled"
EXTERNAL_HTTP = "external_http"
_SUPPORTED_MODES = {DISABLED, EXTERNAL_HTTP}


class ReferenceRelayConfigError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ReferenceRelayConfigService:
    def get_config(self) -> dict[str, Any]:
        return self._summary(get_reference_relay_config())

    def update_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = get_reference_relay_config()
        mode = str(current.get("mode") or DISABLED).strip().lower()
        upload_url = current.get("upload_url")
        token = current.get("token")

        if "mode" in payload:
            mode = str(payload.get("mode") or DISABLED).strip().lower()
            if mode not in _SUPPORTED_MODES:
                raise ReferenceRelayConfigError(400, "Unsupported reference relay mode.")

        if "upload_url" in payload:
            raw_url = str(payload.get("upload_url") or "").strip()
            if raw_url:
                try:
                    parsed = urlparse(raw_url)
                    if parsed.query or parsed.fragment:
                        raise ValueError("query/fragment not allowed")
                    upload_url = validate_provider_probe_url(raw_url)
                except ValueError as exc:
                    raise ReferenceRelayConfigError(400, "Reference relay upload URL is invalid.") from exc
            else:
                upload_url = None

        if "token" in payload:
            token = str(payload.get("token") or "").strip() or None

        if mode == EXTERNAL_HTTP and not upload_url:
            raise ReferenceRelayConfigError(400, "external_http reference relay requires upload_url.")

        updates: dict[str, Any] = {}
        for key, value in (("mode", mode), ("upload_url", upload_url), ("token", token)):
            if key in payload or key == "mode":
                updates[key] = value
        update_reference_relay_config(**updates)
        return self.get_config()

    @staticmethod
    def _summary(config: dict[str, Any]) -> dict[str, Any]:
        mode = str(config.get("mode") or DISABLED)
        return {
            "mode": mode,
            "configured": mode == EXTERNAL_HTTP and bool(config.get("upload_url")),
            "upload_url_configured": bool(config.get("upload_url")),
            "token_configured": bool(config.get("token")),
        }
