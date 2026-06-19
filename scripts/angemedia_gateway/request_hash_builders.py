"""Request hash payload builders for media generation requests."""
from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class RequestHashBuildResult:
    payload: dict[str, Any] | None
    unsupported_reason: str | None = None


IMAGE_EXTRA_ALLOWLIST = frozenset({
    "edit_mode",
    "mode",
    "strength",
    "tags",
    "width",
    "height",
    "steps",
    "guidance_scale",
    "cfg_scale",
    "sampler",
    "scheduler",
})

IMAGE_REFERENCE_KEYS = frozenset({
    "image",
    "images",
    "input_image",
    "input_images",
    "init_image",
    "mask",
    "mask_image",
    "control_image",
    "reference_image",
    "reference_images",
})

VIDEO_EXTRA_BODY_ALLOWLIST = frozenset()

_IMAGE_FIELDS = {
    "prompt",
    "model",
    "n",
    "size",
    "aspect_ratio",
    "response_format",
    "quality",
    "user",
    "safe",
    "negative_prompt",
    "seed",
    "steps",
    "guidance",
    "image",
    "provider_model",
}

_VIDEO_FIELDS = {
    "prompt",
    "model",
    "image",
    "images",
    "mode",
    "height",
    "width",
    "num_frames",
    "frame_rate",
    "negative_prompt",
    "seed",
    "num_inference_steps",
    "extra_body",
    "wait_for_completion",
}

_DENIED_KEY_NAMES = {
    "api_key",
    "provider_api_key",
    "gateway_api_key",
    "authorization",
    "cookie",
    "session",
    "password",
    "secret",
    "token",
    "key_hash",
    "base_url",
    "status_url",
    "quota_url",
    "timestamp",
    "uuid",
    "request_id",
    "job_id",
    "task_id",
    "local_path",
    "file_path",
    "raw_file_path",
    "raw_response",
    "raw_error_body",
    "raw_provider_response",
    "stack_trace",
}


def _normalized_key_name(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")


def _assert_safe_input_key(key: str) -> None:
    normalized = _normalized_key_name(key)
    compact = normalized.replace("_", "")
    if normalized in _DENIED_KEY_NAMES:
        raise ValueError(f"request hash builder input contains forbidden key: {key}")
    if normalized.endswith(("_api_key", "_token", "_secret", "_password", "_session", "_cookie")):
        raise ValueError(f"request hash builder input contains forbidden key: {key}")
    if compact.endswith(("apikey", "token", "secret", "password", "session", "cookie")):
        raise ValueError(f"request hash builder input contains forbidden key: {key}")
    if compact in {
        "baseurl",
        "statusurl",
        "quotaurl",
        "localpath",
        "filepath",
        "rawfilepath",
        "requestid",
        "jobid",
        "taskid",
        "keyhash",
    }:
        raise ValueError(f"request hash builder input contains forbidden key: {key}")


def _assert_safe_mapping_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("request hash builder input keys must be strings")
            _assert_safe_input_key(key)
            _assert_safe_mapping_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_safe_mapping_keys(item)


def _field(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _non_empty_text_field(source: Any, name: str) -> str | None:
    value = _field(source, name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extras(source: Any, known_fields: set[str]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return {key: value for key, value in source.items() if key not in known_fields}
    model_extra = getattr(source, "model_extra", None)
    if isinstance(model_extra, Mapping):
        return dict(model_extra)
    return {}


def _route_target_item(target: Any) -> dict[str, Any]:
    if isinstance(target, Mapping):
        provider = target.get("provider")
        model = target.get("model")
    else:
        provider = getattr(target, "provider", None)
        model = getattr(target, "model", None)
    return {"provider": provider, "model": model}


def _normalize_extra_value(value: Any) -> Any:
    _assert_safe_mapping_keys(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _normalize_extra_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_extra_value(item) for item in value]
    raise TypeError(f"request hash builder extra contains non-JSON value: {type(value).__name__}")


def _safe_path_reference(value: str) -> dict[str, str] | None:
    if not value.startswith(("/uploads/", "/generated/")):
        return None
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    return {"type": "path", "path": parsed.path}


def _data_url_reference(value: str) -> dict[str, str] | None:
    if not value.startswith("data:"):
        return None
    header, sep, data = value.partition(",")
    if not sep or ";base64" not in header:
        return None
    try:
        content = base64.b64decode(data, validate=True)
    except Exception:
        return None
    return {"type": "sha256", "digest": f"sha256:{hashlib.sha256(content).hexdigest()}"}


def _reference_identity(value: Any) -> dict[str, str] | None:
    if isinstance(value, Mapping):
        _assert_safe_mapping_keys(value)
        asset_id = value.get("asset_id")
        if isinstance(asset_id, str) and asset_id:
            return {"type": "asset_id", "id": asset_id}
        digest = value.get("sha256")
        if isinstance(digest, str) and re.fullmatch(r"(sha256:)?[0-9a-fA-F]{64}", digest):
            normalized = digest if digest.startswith("sha256:") else f"sha256:{digest.lower()}"
            return {"type": "sha256", "digest": normalized}
        return None
    if not isinstance(value, str) or not value:
        return None
    if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith(("/", "\\", "file:")):
        path_ref = _safe_path_reference(value)
        return path_ref
    path_ref = _safe_path_reference(value)
    if path_ref is not None:
        return path_ref
    data_ref = _data_url_reference(value)
    if data_ref is not None:
        return data_ref
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        if not parsed.netloc or parsed.query or parsed.fragment:
            return None
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return {"type": "url_sha256", "digest": f"sha256:{digest}"}
    if value.startswith("sha256:") and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", value):
        return {"type": "sha256", "digest": value.lower()}
    return None


def _collect_reference_values(*values: Any) -> list[Any]:
    collected: list[Any] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            collected.extend(value)
        else:
            collected.append(value)
    return collected


def _reference_inputs(values: Iterable[Any]) -> RequestHashBuildResult:
    references: list[dict[str, str]] = []
    for value in values:
        identity = _reference_identity(value)
        if identity is None:
            return RequestHashBuildResult(payload=None, unsupported_reason="unsupported_reference_identity")
        references.append(identity)
    return RequestHashBuildResult(payload={"reference_inputs": references})


def build_image_request_hash_payload(
    req: Any,
    *,
    provider_mode: str,
    resolved_chain: Iterable[Any] | None = None,
    custom_provider_id: str | None = None,
    custom_default_model: str | None = None,
) -> RequestHashBuildResult:
    """Build a stable image request hash payload without touching runtime state."""
    _assert_safe_mapping_keys(req)
    extras = _extras(req, _IMAGE_FIELDS)
    _assert_safe_mapping_keys(extras)

    payload: dict[str, Any] = {
        "kind": "image",
        "route": "v1_images_generations",
        "provider_mode": provider_mode,
        "requested_model": _field(req, "model"),
        "prompt": _field(req, "prompt"),
        "n": _field(req, "n"),
        "size": _field(req, "size"),
        "aspect_ratio": _field(req, "aspect_ratio"),
        "response_format": _field(req, "response_format"),
        "quality": _field(req, "quality"),
        "safe": _field(req, "safe"),
        "negative_prompt": _field(req, "negative_prompt"),
        "seed": _field(req, "seed"),
        "steps": _field(req, "steps"),
        "guidance": _field(req, "guidance"),
    }

    if provider_mode == "builtin":
        payload["resolved_chain"] = [_route_target_item(target) for target in (resolved_chain or [])]
    elif provider_mode == "custom":
        payload["custom_provider_id"] = custom_provider_id
        payload["custom_default_model"] = custom_default_model
        provider_model = _non_empty_text_field(req, "provider_model")
        if provider_model:
            payload["provider_model"] = provider_model
    else:
        raise ValueError(f"unsupported image provider_mode: {provider_mode}")

    extra_payload: dict[str, Any] = {}
    for key, value in extras.items():
        if key in IMAGE_REFERENCE_KEYS:
            continue
        if key == "extra_body" and isinstance(value, Mapping):
            for extra_key, extra_value in value.items():
                _assert_safe_input_key(str(extra_key))
                if extra_key in IMAGE_EXTRA_ALLOWLIST:
                    extra_payload[str(extra_key)] = _normalize_extra_value(extra_value)
            continue
        if key in IMAGE_EXTRA_ALLOWLIST:
            extra_payload[key] = _normalize_extra_value(value)
    if extra_payload:
        payload["extra"] = extra_payload

    reference_values = _collect_reference_values(_field(req, "image"))
    for key in IMAGE_REFERENCE_KEYS:
        reference_values.extend(_collect_reference_values(extras.get(key)))
    if reference_values:
        reference_result = _reference_inputs(reference_values)
        if reference_result.payload is None:
            return reference_result
        payload["reference_inputs"] = reference_result.payload["reference_inputs"]

    return RequestHashBuildResult(payload=payload)


def build_video_request_hash_payload(
    req: Any,
    *,
    provider: str = "agnes_video",
) -> RequestHashBuildResult:
    """Build a stable async video request hash payload without touching runtime state."""
    _assert_safe_mapping_keys(req)
    extra_body = _field(req, "extra_body")
    if extra_body:
        _assert_safe_mapping_keys(extra_body)
        unknown = [key for key in extra_body if key not in VIDEO_EXTRA_BODY_ALLOWLIST]
        if unknown:
            return RequestHashBuildResult(payload=None, unsupported_reason="unsupported_video_extra_body")

    reference_result = _reference_inputs(_collect_reference_values(_field(req, "image"), _field(req, "images")))
    if reference_result.payload is None:
        return reference_result

    payload: dict[str, Any] = {
        "kind": "video",
        "route": "v1_videos",
        "provider": provider,
        "model": _field(req, "model"),
        "prompt": _field(req, "prompt"),
        "mode": _field(req, "mode"),
        "height": _field(req, "height"),
        "width": _field(req, "width"),
        "num_frames": _field(req, "num_frames"),
        "frame_rate": _field(req, "frame_rate"),
        "negative_prompt": _field(req, "negative_prompt"),
        "seed": _field(req, "seed"),
        "num_inference_steps": _field(req, "num_inference_steps"),
    }
    if reference_result.payload is not None and reference_result.payload["reference_inputs"]:
        payload["reference_inputs"] = reference_result.payload["reference_inputs"]
    return RequestHashBuildResult(payload=payload)
