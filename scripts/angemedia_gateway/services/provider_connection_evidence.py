"""连接观察的时效和配置绑定；只证明明确标记的探测范围。"""
from __future__ import annotations

import asyncio
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from ..repositories.provider_connection_evidence import begin_connection_probe, finish_connection_probe, get_connection_evidence
from .provider_connection_test import ProviderConnectionContext, capture_connection_context, probe_builtin_provider_connection
from .provider_evidence_key import EvidenceKeyUnavailable, load_evidence_key

CONNECTION_EVIDENCE_TTL_SECONDS = 15 * 60
PROBE_PENDING_SECONDS = 30
PROBE_CONTRACT_VERSION = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fingerprint(context: ProviderConnectionContext, key: bytes, nonce: str) -> str:
    """随机数随记录保存，真正的 HMAC 密钥只从部署目录读取。"""
    runtime, transport = context.runtime, context.transport
    material = {
        "version": PROBE_CONTRACT_VERSION, "nonce": nonce, "provider": runtime.provider_id,
        "enabled": context.enabled, "api_key": runtime.api_key, "base_url": runtime.base_url,
        "default_override": runtime.default_model_override,
        "endpoint": context.details["endpoint_kind"],
        "transport_invalid": context.transport_invalid,
        "transport": None if transport is None else {"mode": transport.mode, "source": transport.source, "proxy": transport.proxy_url},
    }
    if context.project_model_ids:
        material["project_model_ids"] = context.project_model_ids
    return hmac.digest(key, json.dumps(material, sort_keys=True).encode(), "sha256").hex()


def read_connection_evidence(provider_id: str) -> dict[str, Any]:
    """按当前有效配置判断证据；历史成功不可冒充当前可用性。"""
    row = get_connection_evidence(provider_id)
    now = _now()
    if row is None:
        return {"provider_id": provider_id, "status": "not_tested", "evidence_state": "not_tested", "generation_verified": False, "server_time": now.isoformat()}
    result = json.loads(row["result_json"]) if row["result_json"] else {"provider_id": provider_id, "status": "not_tested", "generation_verified": False}
    nonce = row["fingerprint_key"]
    key = load_evidence_key() if not row["invalidated"] and nonce.startswith("v2:") else None
    if key is None or not hmac.compare_digest(
        row["fingerprint"], _fingerprint(capture_connection_context(provider_id), key, nonce)
    ):
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
    key = await asyncio.to_thread(load_evidence_key, create=True)
    if key is None:
        raise EvidenceKeyUnavailable()
    nonce = "v2:" + secrets.token_hex(16)
    now = _now()
    revision = await asyncio.to_thread(
        begin_connection_probe, provider_id, nonce, _fingerprint(context, key, nonce),
        now.isoformat(), (now + timedelta(seconds=PROBE_PENDING_SECONDS)).isoformat(),
    )
    result = await probe_builtin_provider_connection(provider_id, context=context)
    completed = _now()
    result["checked_at"] = completed.isoformat()
    saved = await asyncio.to_thread(finish_connection_probe, provider_id, revision, result, (completed + timedelta(seconds=CONNECTION_EVIDENCE_TTL_SECONDS)).isoformat())
    current = await asyncio.to_thread(read_connection_evidence, provider_id)
    return {**current, "superseded": not saved}
