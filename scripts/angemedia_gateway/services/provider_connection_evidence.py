"""连接观察的时效和配置绑定；只证明明确标记的探测范围。"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from ..repositories.provider_connection_evidence import begin_connection_probe, finish_connection_probe, get_connection_evidence
from .provider_connection_test import ProviderConnectionContext, capture_connection_context, probe_builtin_provider_connection

CONNECTION_EVIDENCE_TTL_SECONDS = 15 * 60
PROBE_PENDING_SECONDS = 30
PROBE_CONTRACT_VERSION = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fingerprint(context: ProviderConnectionContext, key: str) -> str:
    """每次观察使用私有随机HMAC材料；材料、密钥和指纹均不返回前端。"""
    runtime, transport = context.runtime, context.transport
    material = {
        "version": PROBE_CONTRACT_VERSION, "provider": runtime.provider_id,
        "enabled": context.enabled, "api_key": runtime.api_key, "base_url": runtime.base_url,
        "default_override": runtime.default_model_override,
        "endpoint": context.details["endpoint_kind"],
        "transport_invalid": context.transport_invalid,
        "transport": None if transport is None else {"mode": transport.mode, "source": transport.source, "proxy": transport.proxy_url},
    }
    if context.project_model_ids:
        material["project_model_ids"] = context.project_model_ids
    return hmac.new(bytes.fromhex(key), json.dumps(material, sort_keys=True).encode(), hashlib.sha256).hexdigest()


def read_connection_evidence(provider_id: str) -> dict[str, Any]:
    """按当前有效配置判断证据；历史成功不可冒充当前可用性。"""
    row = get_connection_evidence(provider_id)
    now = _now()
    if row is None:
        return {"provider_id": provider_id, "status": "not_tested", "evidence_state": "not_tested", "generation_verified": False, "server_time": now.isoformat()}
    result = json.loads(row["result_json"]) if row["result_json"] else {"provider_id": provider_id, "status": "not_tested", "generation_verified": False}
    context = capture_connection_context(provider_id)
    if row["invalidated"] or not hmac.compare_digest(row["fingerprint"], _fingerprint(context, row["fingerprint_key"])):
        state = "configuration_changed"
    elif now >= datetime.fromisoformat(row["expires_at"]):
        state = "expired"
    else:
        state = "current" if row["result_json"] else "testing"
    list_validated = state == "current" and result.get("model_list_validated") is True
    if not list_validated:
        result.pop("project_model_visibility", None)
    return {
        **result, "evidence_state": state, "expires_at": row["expires_at"],
        "server_time": now.isoformat(), "ttl_seconds": CONNECTION_EVIDENCE_TTL_SECONDS,
        "evidence_version": PROBE_CONTRACT_VERSION, "generation_verified": False,
        "model_list_validated": list_validated,
    }


async def test_and_record_connection(provider_id: str) -> dict[str, Any]:
    """探测使用冻结配置；落盘只允许最新且未被配置修改作废的请求。"""
    context = await asyncio.to_thread(capture_connection_context, provider_id)
    key = secrets.token_hex(32)
    now = _now()
    revision = await asyncio.to_thread(
        begin_connection_probe, provider_id, key, _fingerprint(context, key),
        now.isoformat(), (now + timedelta(seconds=PROBE_PENDING_SECONDS)).isoformat(),
    )
    result = await probe_builtin_provider_connection(provider_id, context=context)
    completed = _now()
    result["checked_at"] = completed.isoformat()
    saved = await asyncio.to_thread(finish_connection_probe, provider_id, revision, result, (completed + timedelta(seconds=CONNECTION_EVIDENCE_TTL_SECONDS)).isoformat())
    current = await asyncio.to_thread(read_connection_evidence, provider_id)
    return {**current, "superseded": not saved}
