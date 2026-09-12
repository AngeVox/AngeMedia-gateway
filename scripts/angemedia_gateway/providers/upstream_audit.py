"""Read-only upstream model catalog audit helpers.

This module intentionally never mutates the static provider catalog. It only
compares known local provider_model identifiers with fixed official model-list
endpoints and returns a redacted report for human review.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Callable, Mapping, Sequence

import httpx

from .catalog.loader import load_provider_catalog
from .catalog.schema import ProviderCatalog


AUDIT_USER_AGENT = "AngeMedia-Gateway-Upstream-Audit/0.2.12"
AUDIT_TIMEOUT_SECONDS = 12.0


@dataclass(frozen=True)
class RemoteModel:
    canonical_id: str
    aliases: tuple[str, ...] = ()

    @property
    def identities(self) -> frozenset[str]:
        return frozenset({self.canonical_id, *self.aliases})


@dataclass(frozen=True)
class AuditSpec:
    provider_id: str
    endpoint: str | None
    auth_envs: tuple[str, ...] = ()
    requires_auth: bool = False
    query: tuple[tuple[str, str], ...] = ()
    parser: str = "openai_list"
    manual_reason: str | None = None


AUDIT_SPECS: tuple[AuditSpec, ...] = (
    AuditSpec(
        provider_id="openai_image",
        endpoint="https://api.openai.com/v1/models",
        auth_envs=("OPENAI_IMAGE_API_KEY", "OPENAI_API_KEY"),
        requires_auth=True,
        parser="openai_image_models",
    ),
    AuditSpec(
        provider_id="siliconflow",
        endpoint="https://api.siliconflow.cn/v1/models",
        auth_envs=("SILICONFLOW_API_KEY",),
        requires_auth=True,
        query=(("type", "image"),),
        parser="openai_list",
    ),
    AuditSpec(
        provider_id="pollinations",
        endpoint="https://gen.pollinations.ai/image/models",
        auth_envs=("POLLINATIONS_API_KEY",),
        requires_auth=False,
        parser="pollinations_image_models",
    ),
    AuditSpec(
        provider_id="modelscope",
        endpoint=None,
        manual_reason="No verified stable provider-wide image model-list endpoint; review official API-Inference model pages.",
    ),
    AuditSpec(
        provider_id="bytedance",
        endpoint=None,
        manual_reason="BytePlus ModelArk image model IDs are documentation/version driven; no verified public provider-wide image list is used.",
    ),
    AuditSpec(
        provider_id="agnes_image",
        endpoint=None,
        manual_reason="Agnes image versions are documentation driven; no verified stable model-list endpoint is used.",
    ),
    AuditSpec(
        provider_id="agnes_video",
        endpoint=None,
        manual_reason="Agnes video versions are documentation driven; no verified stable model-list endpoint is used.",
    ),
)


FetchJson = Callable[[AuditSpec, Mapping[str, str]], Any]


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _first_secret(environ: Mapping[str, str], names: Sequence[str]) -> str:
    for name in names:
        value = _clean_text(environ.get(name))
        if value:
            return value
    return ""


def _parse_openai_list(payload: Any) -> list[RemoteModel]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("upstream model list response must contain data[]")
    result: list[RemoteModel] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = _clean_text(row.get("id"))
        if model_id and model_id not in seen:
            seen.add(model_id)
            result.append(RemoteModel(model_id))
    return result


def _parse_openai_image_models(payload: Any) -> list[RemoteModel]:
    return [model for model in _parse_openai_list(payload) if model.canonical_id.startswith("gpt-image-")]


def _parse_pollinations_image_models(payload: Any) -> list[RemoteModel]:
    if not isinstance(payload, list):
        raise ValueError("Pollinations image model list response must be an array")
    result: list[RemoteModel] = []
    seen: set[str] = set()
    for row in payload:
        if not isinstance(row, dict):
            continue
        output_modalities = row.get("output_modalities")
        if isinstance(output_modalities, list) and "image" not in {
            _clean_text(item).lower() for item in output_modalities
        }:
            continue
        canonical = _clean_text(row.get("name") or row.get("id") or row.get("model"))
        if not canonical or canonical in seen:
            continue
        aliases_value = row.get("aliases")
        aliases = tuple(
            dict.fromkeys(
                _clean_text(item)
                for item in (aliases_value if isinstance(aliases_value, list) else [])
                if _clean_text(item) and _clean_text(item) != canonical
            )
        )
        seen.add(canonical)
        result.append(RemoteModel(canonical, aliases))
    return result


def parse_remote_models(spec: AuditSpec, payload: Any) -> list[RemoteModel]:
    if spec.parser == "openai_list":
        return _parse_openai_list(payload)
    if spec.parser == "openai_image_models":
        return _parse_openai_image_models(payload)
    if spec.parser == "pollinations_image_models":
        return _parse_pollinations_image_models(payload)
    raise ValueError(f"unsupported upstream audit parser: {spec.parser}")


def _default_fetch_json(spec: AuditSpec, headers: Mapping[str, str]) -> Any:
    if not spec.endpoint:
        raise ValueError("audit endpoint is not configured")
    with httpx.Client(
        timeout=AUDIT_TIMEOUT_SECONDS,
        trust_env=False,
        follow_redirects=False,
        headers=dict(headers),
    ) as client:
        response = client.get(spec.endpoint, params=dict(spec.query))
        response.raise_for_status()
        return response.json()


def _local_models(catalog: ProviderCatalog, provider_id: str) -> list[str]:
    return sorted({
        model.provider_model
        for model in catalog.models
        if model.provider == provider_id and model.selectable and model.provider_model
    })


def _compare_models(local_models: Sequence[str], remote_models: Sequence[RemoteModel]) -> dict[str, Any]:
    remote_identity_to_canonical: dict[str, str] = {}
    for remote in remote_models:
        for identity in remote.identities:
            remote_identity_to_canonical.setdefault(identity, remote.canonical_id)

    matched: dict[str, str] = {}
    missing_upstream: list[str] = []
    represented_remote: set[str] = set()
    for local in local_models:
        canonical = remote_identity_to_canonical.get(local)
        if canonical:
            matched[local] = canonical
            represented_remote.add(canonical)
        else:
            missing_upstream.append(local)

    candidates = sorted(
        remote.canonical_id
        for remote in remote_models
        if remote.canonical_id not in represented_remote
    )
    return {
        "local_models": list(local_models),
        "matched": matched,
        "missing_upstream": sorted(missing_upstream),
        "upstream_candidates": candidates,
        "upstream_count": len(remote_models),
    }


def audit_upstream_models(
    *,
    catalog: ProviderCatalog | None = None,
    environ: Mapping[str, str] | None = None,
    fetch_json: FetchJson | None = None,
    provider_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Compare local catalog models to fixed upstream model-list endpoints.

    The return value never includes API keys, request headers, response bodies,
    or arbitrary remote URLs. Upstream additions are review candidates only.
    """

    loaded_catalog = catalog or load_provider_catalog()
    env = environ if environ is not None else os.environ
    fetcher = fetch_json or _default_fetch_json
    selected = set(provider_ids or ())
    unknown = selected - {spec.provider_id for spec in AUDIT_SPECS}
    if unknown:
        raise ValueError(f"unknown audit provider: {sorted(unknown)[0]}")

    providers: dict[str, Any] = {}
    for spec in AUDIT_SPECS:
        if selected and spec.provider_id not in selected:
            continue
        local = _local_models(loaded_catalog, spec.provider_id)
        if spec.manual_reason:
            providers[spec.provider_id] = {
                "status": "manual_review",
                "local_models": local,
                "reason": spec.manual_reason,
            }
            continue

        secret = _first_secret(env, spec.auth_envs)
        if spec.requires_auth and not secret:
            providers[spec.provider_id] = {
                "status": "skipped_missing_key",
                "local_models": local,
            }
            continue

        headers = {"Accept": "application/json", "User-Agent": AUDIT_USER_AGENT}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        try:
            payload = fetcher(spec, headers)
            remote = parse_remote_models(spec, payload)
            comparison = _compare_models(local, remote)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            providers[spec.provider_id] = {
                "status": "error",
                "local_models": local,
                "error_type": type(exc).__name__,
            }
            continue
        providers[spec.provider_id] = {"status": "ok", **comparison}

    return {
        "schema_version": 1,
        "mode": "read_only_review",
        "providers": providers,
    }
