"""Deterministic read-only tool planning for AngeMedia assistant chat."""
from __future__ import annotations

import re
from typing import Any

from ..providers.catalog.loader import load_provider_catalog
from .assistant_skills import AssistantSkill, load_assistant_skill

_JOB_ID_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
_JOB_TERMS = ("job", "任务", "失败", "failed", "报错", "error", "重试", "retry")
_SYSTEM_TERMS = (
    "diagnostic", "diagnostics", "queue", "worker", "dispatcher", "redis", "celery", "database", "storage",
    "诊断", "队列", "工作进程", "数据库", "存储", "积压", "卡住", "离线", "日志", "log", "logs",
)
_CHANNEL_TERMS = (
    "channel", "provider", "model", "configuration", "config", "api key", "apikey",
    "connection", "connect", "network", "probe", "reachable",
    "渠道", "服务商", "模型", "配置", "密钥", "连接", "连通", "网络", "探测",
)
_CONNECTION_TERMS = (
    "test connection", "connection test", "connect", "connection", "network", "probe", "reachable",
    "连接测试", "测试连接", "连接失败", "连不上", "连通", "网络", "探测", "超时",
)


def extract_job_id(message: str) -> str | None:
    match = _JOB_ID_RE.search(str(message or ""))
    return match.group(0) if match else None


def _catalog_terms() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    try:
        catalog = load_provider_catalog()
    except Exception:
        return [], []
    providers: list[tuple[str, str]] = []
    for item in catalog.providers:
        providers.append((item.id.lower(), item.id))
        providers.append((item.display_name.lower(), item.id))
    models: list[tuple[str, str]] = []
    for item in catalog.models:
        for value in (item.id, item.provider_model, *item.aliases):
            if value:
                models.append((str(value).lower(), item.id))
    providers.sort(key=lambda item: len(item[0]), reverse=True)
    models.sort(key=lambda item: len(item[0]), reverse=True)
    return providers, models


def extract_provider_id(message: str) -> str | None:
    lowered = str(message or "").lower()
    providers, _ = _catalog_terms()
    for token, provider_id in providers:
        if token and token in lowered:
            return provider_id
    return None


def extract_model_id(message: str) -> str | None:
    lowered = str(message or "").lower()
    _, models = _catalog_terms()
    for token, model_id in models:
        if token and token in lowered:
            return model_id
    return None


def select_chat_skill(message: str) -> AssistantSkill:
    text = str(message or "")
    lowered = text.lower()
    if extract_job_id(text):
        return load_assistant_skill("job_diagnostician")
    if any(term in lowered for term in _SYSTEM_TERMS):
        return load_assistant_skill("system_diagnostician")
    if extract_provider_id(text) or extract_model_id(text) or any(term in lowered for term in _CHANNEL_TERMS):
        return load_assistant_skill("channel_config_advisor")
    if any(term in lowered for term in _JOB_TERMS):
        return load_assistant_skill("job_diagnostician")
    return load_assistant_skill("angemedia_faq")


def plan_assistant_tools(message: str, skill: AssistantSkill) -> list[tuple[str, dict[str, Any]]]:
    text = str(message or "").strip()
    calls: list[tuple[str, dict[str, Any]]] = []
    if skill.id == "job_diagnostician":
        job_id = extract_job_id(text)
        if job_id:
            calls.extend([
                ("job_safe_summary", {"job_id": job_id}),
                ("failure_diagnostic", {"job_id": job_id}),
            ])
        if "local_knowledge_base" in skill.allowed_tools:
            calls.append(("local_knowledge_base", {"query": text, "limit": 3}))
    elif skill.id == "system_diagnostician":
        calls.append(("diagnostics_summary", {}))
        lowered = text.lower()
        if any(term in lowered for term in ("queue", "worker", "dispatcher", "redis", "celery", "队列", "积压", "卡住")):
            calls.append(("queue_status", {}))
        if any(term in lowered for term in ("log", "logs", "日志")):
            calls.append(("recent_logs", {}))
        calls.append(("local_knowledge_base", {"query": text, "limit": 3}))
    elif skill.id == "channel_config_advisor":
        provider_id = extract_provider_id(text)
        calls.append(("channel_safe_summary", {"provider_id": provider_id} if provider_id else {}))
        model_id = extract_model_id(text)
        if model_id:
            calls.append(("catalog_model_capabilities", {"model": model_id}))
        lowered = text.lower()
        wants_connection = provider_id is not None and any(term in lowered for term in _CONNECTION_TERMS)
        if wants_connection:
            calls.append(("provider_connection_test", {"provider_id": provider_id}))
            calls.append(("network_probe", {"provider_id": provider_id}))
        if "local_knowledge_base" in skill.allowed_tools and len(calls) < 4:
            calls.append(("local_knowledge_base", {"query": text, "limit": 3}))
    else:
        calls.append(("local_knowledge_base", {"query": text, "limit": 4}))
    return calls[:4]
