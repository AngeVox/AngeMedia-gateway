"""Read-only tool registry and executor for the AngeMedia assistant.

The assistant never executes arbitrary shell commands or Provider mutations.
Every tool is registered explicitly, checked against the selected skill's
``allowed_tools`` list, and sanitized again before it reaches an LLM or UI.
"""
from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

from ..error_diagnostics import classify_provider_error
from ..job_sanitizer import sanitize_error_text
from ..providers.catalog.loader import load_provider_catalog
from ..repositories.assets import list_assets
from ..repositories.job_attempts import list_job_attempts
from ..repositories.job_dispatches import list_job_dispatches
from ..repositories.jobs import get_job
from ..repositories.settings import list_custom_providers
from ..security import redact_secret_text, validate_task_id
from .assistant_knowledge_base import search_assistant_knowledge
from .assistant_skills import AssistantSkill
from .assistant_network_probe import probe_builtin_provider_network
from .diagnostics_summary import DiagnosticsSummaryService
from .provider_connection_test import probe_builtin_provider_connection
from .provider_runtime_config import ProviderRuntimeConfigService

ToolHandler = Callable[[dict[str, Any]], Any]

_SENSITIVE_KEY_PARTS = (
    "api_key", "apikey", "password", "secret", "authorization", "cookie", "token",
    "input_json", "output_json", "request_hash", "local_path", "raw_body", "provider_raw",
    "broker_url", "base_url_override",
)
_LOCAL_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:root|home|tmp|var|mnt|vol\d*|app)(?:/[^\s,;，。)]*)?")
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s,;，。]+")
_DATA_URL_RE = re.compile(r"data:[A-Za-z0-9.+/-]+;base64,[A-Za-z0-9+/=_-]+", re.I)
_SIGNED_URL_RE = re.compile(
    r"https?://[^\s]+(?:[?&](?:x-amz-|signature=|sig=|token=|key=|expires=)[^\s]*)",
    re.I,
)


class AssistantToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssistantToolDefinition:
    name: str
    handler: ToolHandler
    read_only: bool = True


@dataclass(frozen=True)
class AssistantToolResult:
    tool: str
    status: str
    summary: str
    data: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "status": self.status,
            "summary": self.summary,
            "data": self.data,
        }


def _is_sensitive_key(key: Any) -> bool:
    lowered = str(key or "").strip().lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _sanitize_string(value: Any, *, limit: int = 1200) -> str:
    text = redact_secret_text(str(value or ""))
    text = re.sub(r"\bAuthorization\b(?:\s*:\s*Bearer\s+[^\s,;]+)?", "[redacted auth]", text, flags=re.I)
    text = _DATA_URL_RE.sub("[redacted data url]", text)
    text = _SIGNED_URL_RE.sub("[redacted signed url]", text)
    text = _WINDOWS_PATH_RE.sub("[redacted local path]", text)
    text = _LOCAL_PATH_RE.sub("[redacted local path]", text)
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    return text[:limit]


def sanitize_assistant_tool_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 100:
                break
            if _is_sensitive_key(key):
                continue
            out[str(key)[:128]] = sanitize_assistant_tool_value(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [sanitize_assistant_tool_value(item, depth=depth + 1) for item in list(value)[:50]]
    return _sanitize_string(value)


def _controlled_asset_path(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlsplit(text)
    if parsed.query or parsed.fragment:
        return None
    path = parsed.path if parsed.scheme or parsed.netloc else text
    if path.startswith(("/generated/", "/uploads/")):
        return path[:512]
    return None


def _require_job_id(args: dict[str, Any]) -> str:
    try:
        return validate_task_id(str(args.get("job_id") or ""))
    except ValueError as exc:
        raise AssistantToolError("invalid job id") from exc


def _job_safe_summary(args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    job_id = _require_job_id(args)
    job = get_job(job_id)
    if job is None:
        raise AssistantToolError("job not found")
    assets = list_assets(job_id=job_id, limit=10, offset=0)
    asset_paths = [
        path
        for path in (_controlled_asset_path(item.get("url_path")) for item in assets)
        if path
    ]
    attempts = list_job_attempts(job_id)[-5:]
    dispatches = list_job_dispatches(job_id, limit=100)
    dispatch_counts: dict[str, int] = {}
    for dispatch in dispatches:
        state = str(dispatch.get("status") or "unknown")
        dispatch_counts[state] = dispatch_counts.get(state, 0) + 1
    latest_dispatch = dispatches[-1] if dispatches else None
    data = {
        "job_id": job_id,
        "kind": job.get("kind"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "provider": sanitize_error_text(job.get("provider"), limit=128),
        "model": sanitize_error_text(job.get("model"), limit=160),
        "provider_status": sanitize_error_text(job.get("provider_status"), limit=96),
        "error_code": sanitize_error_text(job.get("error_code"), limit=128),
        "error_category": sanitize_error_text(job.get("error_category"), limit=128),
        "human_hint": sanitize_error_text(job.get("human_hint"), limit=300),
        "retryable": bool(job.get("retryable")),
        "gateway_stage": sanitize_error_text(job.get("gateway_stage"), limit=128),
        "attempt_count": int(job.get("attempt_count") or 0),
        "max_attempts": int(job.get("max_attempts") or 0),
        "worker_kind": sanitize_error_text(job.get("worker_kind"), limit=64),
        "external_task_present": bool(job.get("external_task_id")),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "asset_count": len(assets),
        "asset_paths": asset_paths,
        "recent_attempts": [
            {
                "attempt": item.get("attempt_number"),
                "stage": item.get("stage"),
                "status": item.get("status"),
                "error_code": sanitize_error_text(item.get("error_code"), limit=128),
                "retry_at": item.get("retry_at"),
            }
            for item in attempts
        ],
        "dispatch": {
            "total": len(dispatches),
            "status_counts": dispatch_counts,
            "latest_status": None if latest_dispatch is None else latest_dispatch.get("status"),
            "latest_error": None if latest_dispatch is None else sanitize_error_text(latest_dispatch.get("last_error"), limit=240),
        },
    }
    summary = f"job {job_id}: {job.get('status')} / {job.get('stage')}"
    if job.get("error_code"):
        summary += f" / {sanitize_error_text(job.get('error_code'), limit=80)}"
    return data, summary


def _failure_diagnostic(args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    job_id = _require_job_id(args)
    job = get_job(job_id)
    if job is None:
        raise AssistantToolError("job not found")
    classification = {
        "error_category": job.get("error_category"),
        "human_hint": job.get("human_hint"),
        "retryable": bool(job.get("retryable")),
        "gateway_stage": job.get("gateway_stage"),
    }
    if not classification["error_category"] and job.get("error_message"):
        classification = classify_provider_error(sanitize_error_text(job.get("error_message"), limit=500))
    data = {
        "job_id": job_id,
        "status": job.get("status"),
        "stage": job.get("stage"),
        "error_code": sanitize_error_text(job.get("error_code"), limit=128),
        "error_category": sanitize_error_text(classification.get("error_category"), limit=128),
        "human_hint": sanitize_error_text(classification.get("human_hint"), limit=300),
        "retryable": bool(classification.get("retryable")),
        "gateway_stage": sanitize_error_text(classification.get("gateway_stage"), limit=128),
    }
    summary = (
        f"failure diagnostic: {data['error_category'] or data['error_code'] or 'no classified failure'}"
        + (f"; {data['human_hint']}" if data.get("human_hint") else "")
    )
    return data, summary


def _diagnostics_summary(_args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    data = DiagnosticsSummaryService().summary()
    runtime = data.get("runtime") or {}
    queue = data.get("queue") or {}
    database = data.get("database") or {}
    summary = (
        f"runtime {runtime.get('version') or 'unknown'}; "
        f"queue={queue.get('backend') or 'unknown'} healthy={bool(queue.get('healthy'))}; "
        f"database_reachable={bool(database.get('reachable'))}"
    )
    return data, summary


def _queue_status(_args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    full = DiagnosticsSummaryService().summary()
    data = {
        "queue": full.get("queue") or {},
        "dispatches": full.get("dispatches") or {},
    }
    queue = data["queue"]
    summary = (
        f"queue backend={queue.get('backend') or 'unknown'} "
        f"healthy={bool(queue.get('healthy'))} active={int(queue.get('active_total') or 0)}"
    )
    return data, summary


def _channel_safe_summary(args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    requested = str(args.get("provider_id") or "").strip()
    builtins = []
    for item in ProviderRuntimeConfigService().list_configs():
        builtins.append({
            "provider_id": item.get("provider_id"),
            "display_name": item.get("display_name"),
            "media_types": item.get("media_types") or [],
            "enabled": bool(item.get("enabled")),
            "key_configured": bool(item.get("api_key_configured")),
            "base_url_overridden": bool(item.get("base_url_override")),
            "default_model": item.get("default_model_override") or item.get("default_model"),
            "source": "builtin",
        })
    customs = []
    for item in list_custom_providers(include_secret=False):
        customs.append({
            "provider_id": item.get("id"),
            "display_name": item.get("name"),
            "media_types": ["image"],
            "enabled": bool(item.get("enabled")),
            "key_configured": bool(item.get("api_key")),
            "base_url_overridden": bool(item.get("base_url")),
            "default_model": item.get("default_model"),
            "source": "custom",
        })
    channels = builtins + customs
    if requested:
        channels = [item for item in channels if str(item.get("provider_id")) == requested]
        if not channels:
            raise AssistantToolError("channel not found")
    data = {"channels": channels, "total": len(channels)}
    summary = (
        f"channel {channels[0]['provider_id']}: enabled={channels[0]['enabled']} key_configured={channels[0]['key_configured']}"
        if len(channels) == 1
        else f"{len(channels)} safe channel summaries"
    )
    return data, summary


def _require_builtin_provider_id(args: dict[str, Any]) -> str:
    provider_id = str(args.get("provider_id") or "").strip()
    if not provider_id:
        raise AssistantToolError("provider id is required")
    known = {str(item.get("provider_id") or "") for item in ProviderRuntimeConfigService().list_configs()}
    if provider_id not in known:
        raise AssistantToolError("built-in provider not found")
    return provider_id


async def _provider_connection_test(args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    provider_id = _require_builtin_provider_id(args)
    data = await probe_builtin_provider_connection(provider_id)
    status = str(data.get("status") or "unknown")
    return data, f"provider connection {provider_id}: {status}"


async def _network_probe(args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    provider_id = _require_builtin_provider_id(args)
    data = await probe_builtin_provider_network(provider_id)
    status = str(data.get("status") or "unknown")
    dns_class = str(data.get("dns_class") or "unknown")
    return data, f"network probe {provider_id}: {status}; dns={dns_class}"


def _find_catalog_model(query: str):
    catalog = load_provider_catalog()
    wanted = query.strip().lower()
    exact = []
    for model in catalog.models:
        values = {model.id.lower(), model.provider_model.lower(), *(alias.lower() for alias in model.aliases)}
        if wanted in values:
            exact.append(model)
    if not exact:
        return None
    exact.sort(key=lambda item: (item.status == "reserved", item.id))
    return exact[0]


def _catalog_model_capabilities(args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    query = str(args.get("model") or "").strip()
    if not query:
        raise AssistantToolError("model is required")
    model = _find_catalog_model(query)
    if model is None:
        raise AssistantToolError("model not found in catalog")
    operations = {
        name: {
            "supported": bool(spec.supported),
            "reference_roles": sorted({role for ref in spec.refs for role in ref.roles}),
            "reference_max_total": max((ref.max_total or 0 for ref in spec.refs), default=0) or None,
        }
        for name, spec in model.operations.items()
    }
    data = {
        "model_id": model.id,
        "provider": model.provider,
        "provider_model": model.provider_model,
        "media_type": model.media_type,
        "display_name": model.display_name,
        "status": model.status,
        "selectable": model.selectable,
        "capabilities": dict(model.capabilities),
        "size_presets": list(model.size_presets),
        "operations": operations,
        "tags": list(model.tags),
    }
    supported = [name for name, value in model.capabilities.items() if value]
    return data, f"{model.id}: {', '.join(supported) if supported else 'no declared capabilities'}"


def _local_knowledge_base(args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise AssistantToolError("knowledge query is required")
    hits = search_assistant_knowledge(query, limit=int(args.get("limit") or 4))
    return {"hits": hits, "count": len(hits)}, f"local knowledge: {len(hits)} hit(s)"


class AssistantToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AssistantToolDefinition] = {}
        for name, handler in (
            ("local_knowledge_base", _local_knowledge_base),
            ("job_safe_summary", _job_safe_summary),
            ("failure_diagnostic", _failure_diagnostic),
            ("diagnostics_summary", _diagnostics_summary),
            ("queue_status", _queue_status),
            ("channel_safe_summary", _channel_safe_summary),
            ("catalog_model_capabilities", _catalog_model_capabilities),
            ("provider_connection_test", _provider_connection_test),
            ("network_probe", _network_probe),
        ):
            self.register(AssistantToolDefinition(name=name, handler=handler, read_only=True))

    def register(self, definition: AssistantToolDefinition) -> None:
        if not definition.name or definition.name in self._tools:
            raise ValueError("assistant tool name is invalid or duplicated")
        if not definition.read_only:
            raise ValueError("P1 assistant registry accepts read-only tools only")
        self._tools[definition.name] = definition

    def get(self, name: str) -> AssistantToolDefinition | None:
        return self._tools.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))


class AssistantToolExecutor:
    def __init__(self, registry: AssistantToolRegistry | None = None) -> None:
        self.registry = registry or AssistantToolRegistry()

    def _definition(self, skill: AssistantSkill, tool_name: str) -> AssistantToolDefinition:
        if tool_name not in skill.allowed_tools:
            raise AssistantToolError(f"tool is not allowed by skill: {tool_name}")
        definition = self.registry.get(tool_name)
        if definition is None:
            raise AssistantToolError(f"tool is not registered: {tool_name}")
        return definition

    @staticmethod
    def _result(tool_name: str, payload: Any) -> AssistantToolResult:
        try:
            data, summary = payload
        except (TypeError, ValueError) as exc:
            raise AssistantToolError("tool returned an invalid result") from exc
        safe_data = sanitize_assistant_tool_value(data)
        if not isinstance(safe_data, dict):
            safe_data = {"result": safe_data}
        return AssistantToolResult(
            tool=tool_name,
            status="done",
            summary=_sanitize_string(summary, limit=240),
            data=safe_data,
        )

    def execute(self, skill: AssistantSkill, tool_name: str, args: dict[str, Any] | None = None) -> AssistantToolResult:
        definition = self._definition(skill, tool_name)
        if inspect.iscoroutinefunction(definition.handler):
            raise AssistantToolError(f"async tool requires execute_async: {tool_name}")
        try:
            payload = definition.handler(dict(args or {}))
            if inspect.isawaitable(payload):
                close = getattr(payload, "close", None)
                if callable(close):
                    close()
                raise AssistantToolError(f"async tool requires execute_async: {tool_name}")
        except AssistantToolError:
            raise
        except Exception as exc:
            raise AssistantToolError(f"tool execution failed: {type(exc).__name__}") from exc
        return self._result(tool_name, payload)

    async def execute_async(
        self,
        skill: AssistantSkill,
        tool_name: str,
        args: dict[str, Any] | None = None,
    ) -> AssistantToolResult:
        definition = self._definition(skill, tool_name)
        try:
            payload = definition.handler(dict(args or {}))
            if inspect.isawaitable(payload):
                payload = await payload
        except AssistantToolError:
            raise
        except Exception as exc:
            raise AssistantToolError(f"tool execution failed: {type(exc).__name__}") from exc
        return self._result(tool_name, payload)
