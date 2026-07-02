"""Scoped AngeMedia assistant chat service."""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .. import config as C
from ..assistant import assistant_enabled
from ..repositories.settings import get_config
from ..security import redact_secret_text
from ..repositories.assistant_sessions import (
    add_assistant_message,
    add_assistant_run,
    create_assistant_session,
    get_assistant_session,
    list_assistant_messages,
)
from .assistant_config_service import resolve_assistant_runtime
from .assistant_skills import safe_tool_event

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
KB_ROOT = C.PROJECT_ROOT / "docs" / "assistant" / "kb"

SCOPE_TERMS = (
    "angemedia", "gateway", "studio", "generate", "image", "video", "job", "jobs", "asset", "assets",
    "channel", "provider", "model", "agnes", "queue", "worker", "dispatcher", "redis", "celery",
    "dashboard", "diagnostics", "api key", "apikey", "llm", "assistant", "prompt", "timeout",
    "生成", "图片", "视频", "任务", "资产", "渠道", "服务商", "模型", "队列", "小助手", "提示词",
    "诊断", "超时", "密钥", "配置", "尺寸", "图生图", "图生视频", "文生图", "文生视频",
)


def _safe_text(value: Any, *, limit: int = 4000) -> str:
    text = redact_secret_text(str(value or ""))
    text = re.sub(r"\bAuthorization\b(?:\s*:\s*Bearer\s+[A-Za-z0-9_.-]+)?", "[redacted auth]", text, flags=re.I)
    text = re.sub(r"\bprovider[_ -]?raw[_ -]?body\b", "[redacted provider body]", text, flags=re.I)
    text = re.sub(r"data:[A-Za-z0-9.+/-]+;base64,[A-Za-z0-9+/=_-]+", "[redacted data url]", text, flags=re.I)
    text = re.sub(r"\b[A-Za-z]:\\[^\s,;，。]+", "[redacted local path]", text)
    text = re.sub(r"request_hash\s*[:=]\s*[A-Za-z0-9_.:-]+", "[redacted hash]", text, flags=re.I)
    return " ".join(text.split())[:limit]


def _language(message: str, requested: str | None = None) -> str:
    if requested in {"zh", "en"}:
        return requested
    return "zh" if CJK_RE.search(message or "") else "en"


def _in_scope(message: str) -> bool:
    lowered = message.lower()
    return any(term.lower() in lowered for term in SCOPE_TERMS)


def _kb_documents() -> list[tuple[str, str]]:
    docs: list[tuple[str, str]] = []
    if not KB_ROOT.exists():
        return docs
    for path in sorted(KB_ROOT.glob("*.md")):
        try:
            resolved = path.resolve()
            if KB_ROOT.resolve() not in resolved.parents:
                continue
            docs.append((path.stem, path.read_text(encoding="utf-8")))
        except OSError:
            continue
    return docs


def _score_paragraph(query_terms: set[str], paragraph: str) -> int:
    lowered = paragraph.lower()
    return sum(1 for term in query_terms if term and term in lowered)


def _search_kb(message: str, *, limit: int = 4) -> list[dict[str, str]]:
    terms = {term.lower() for term in re.split(r"[\s,，。；;:/\\|()]+", message) if len(term.strip()) >= 2}
    for scope_term in SCOPE_TERMS:
        if scope_term.lower() in message.lower():
            terms.add(scope_term.lower())
    hits: list[tuple[int, str, str]] = []
    for doc_id, body in _kb_documents():
        for paragraph in re.split(r"\n\s*\n", body):
            clean = _safe_text(paragraph, limit=900)
            if not clean or clean.startswith("# "):
                continue
            score = _score_paragraph(terms, clean)
            if score > 0:
                hits.append((score, doc_id, clean))
    hits.sort(key=lambda item: item[0], reverse=True)
    return [{"source": doc_id, "summary": summary} for _, doc_id, summary in hits[:limit]]


def _format_answer(message: str, hits: list[dict[str, str]], language: str) -> str:
    if not hits:
        return (
            "我只找到了有限的本地知识。这个问题属于 AngeMedia 范围，但当前 KB 还缺资料；"
            "你可以补充具体页面、任务 ID、渠道或错误信息。"
            if language == "zh"
            else "I found limited local knowledge. This is in AngeMedia scope, but the bundled KB needs more detail. Add the page, job id, channel, or error."
        )
    if language == "zh":
        lines = ["基于本地 AngeMedia 知识库，我建议这样处理："]
        for hit in hits:
            lines.append(f"- {hit['summary']}")
        lines.append("我不会调用外网，也不会返回密钥、原始响应、签名 URL 或本地路径。")
        return "\n".join(lines)
    lines = ["Based on the bundled AngeMedia knowledge base:"]
    for hit in hits:
        lines.append(f"- {hit['summary']}")
    lines.append("I did not call the web and will not expose keys, raw responses, signed URLs, or local paths.")
    return "\n".join(lines)


def _refusal(language: str) -> str:
    if language == "zh":
        return "我只能回答 AngeMedia Gateway / Studio / 队列 / 生成 / 渠道 / 资产 / 诊断相关问题。这个问题超出范围。"
    return "I can only answer AngeMedia Gateway, Studio, queue, generation, channel, asset, and diagnostics questions. This request is out of scope."


def _event(language: str, tool: str, zh: str, en: str, *, status: str = "done") -> dict[str, str]:
    return safe_tool_event(tool, zh if language == "zh" else en, status=status)


def _llm_chat_configured() -> bool:
    runtime = resolve_assistant_runtime()
    return bool(assistant_enabled() and runtime.base_url and runtime.model)


def _chat_messages(message: str, hits: list[dict[str, str]], language: str) -> list[dict[str, str]]:
    context = {
        "language": language,
        "question": message,
        "safe_kb_hits": hits,
        "allowed_scope": list(SCOPE_TERMS),
    }
    system = (
        "You are the scoped AngeMedia Studio assistant. Answer only AngeMedia Gateway / Studio / queue / "
        "generation / channels / assets / diagnostics / configuration questions. If the request is out of scope, "
        "refuse briefly. Use the safe context and do not invent operational facts. Never reveal API keys, "
        "Authorization headers, raw provider bodies, request hashes, signed URLs, data URLs, or local filesystem paths. "
        "For Chinese language requests, answer in Chinese. For English requests, answer in English. "
        "When diagnosing failures, give concrete next checks and mention that Jobs detail and Diagnostics contain the safe evidence."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": str(context)},
    ]


async def _call_llm_chat(message: str, hits: list[dict[str, str]], language: str) -> tuple[str, int]:
    runtime = resolve_assistant_runtime()
    if not runtime.base_url or not runtime.model:
        raise RuntimeError("assistant LLM is not configured")
    try:
        timeout = float(get_config("ANGE_LLM_TIMEOUT", "60"))
        temperature = float(get_config("ANGE_LLM_TEMPERATURE", "0.2"))
    except ValueError:
        timeout, temperature = 60.0, 0.2
    headers = {"Content-Type": "application/json"}
    if runtime.api_key:
        headers["Authorization"] = f"Bearer {runtime.api_key}"
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{runtime.base_url}/chat/completions",
            headers=headers,
            json={
                "model": runtime.model,
                "temperature": temperature,
                "max_tokens": 900,
                "messages": _chat_messages(message, hits, language),
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"assistant LLM failed: HTTP {resp.status_code}")
    data = resp.json()
    content = str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
    if not content:
        raise RuntimeError("assistant LLM returned empty content")
    return _safe_text(content, limit=4000), int((time.perf_counter() - started) * 1000)


async def build_assistant_chat_reply(payload: dict[str, Any]) -> dict[str, Any]:
    message = _safe_text(payload.get("message"), limit=4000)
    if not message:
        raise ValueError("message is required")
    language = _language(message, payload.get("language"))
    session_id = _safe_text(payload.get("session_id"), limit=64)
    session = get_assistant_session(session_id) if session_id else None
    if not session:
        session_id = uuid.uuid4().hex
        session = create_assistant_session(session_id, message[:80] or "AngeMedia Assistant")

    user_message = add_assistant_message(uuid.uuid4().hex, session_id, "user", message)
    timeline = [_event(language, "scope_guard", "已检查 AngeMedia 专用助手范围", "checked AngeMedia-only assistant scope")]
    status = "succeeded"
    skill_id = "angemedia_faq"
    if not _in_scope(message):
        answer = _refusal(language)
        hits: list[dict[str, str]] = []
        status = "refused"
        timeline.append(_event(language, "scope_guard", "问题超出 AngeMedia 范围，已拒绝", "request refused as out of scope", status="refused"))
    else:
        hits = _search_kb(message)
        timeline.append(_event(language, "local_kb_search", f"找到 {len(hits)} 条安全本地知识", f"found {len(hits)} safe KB hit(s)"))
        if _llm_chat_configured():
            try:
                answer, elapsed_ms = await _call_llm_chat(message, hits, language)
                timeline.append(_event(language, "llm_chat", f"已调用已配置 LLM，耗时 {elapsed_ms}ms", f"answered with configured LLM in {elapsed_ms}ms"))
                skill_id = "angemedia_llm_chat"
            except Exception as exc:
                timeline.append(_event(language, "llm_chat", f"LLM 调用失败，已回退本地知识：{redact_secret_text(str(exc))}", f"LLM failed; used local KB fallback: {redact_secret_text(str(exc))}", status="fallback"))
                answer = _format_answer(message, hits, language)
        else:
            timeline.append(_event(language, "llm_chat", "LLM 未启用或未配置，已使用本地知识回退", "LLM disabled or not configured; used local KB fallback", status="skipped"))
            answer = _format_answer(message, hits, language)

    assistant_message = add_assistant_message(
        uuid.uuid4().hex,
        session_id,
        "assistant",
        answer,
        {"kb_hits": hits, "status": status},
    )
    run = add_assistant_run(
        uuid.uuid4().hex,
        session_id,
        status,
        skill_id,
        {"message": message, "language": language},
        {"answer": answer, "kb_hits": hits},
        timeline,
    )
    messages = list_assistant_messages(session_id)
    return {
        "session": session,
        "session_id": session_id,
        "message": assistant_message,
        "user_message": user_message,
        "answer": answer,
        "status": status,
        "timeline": timeline,
        "run": run,
        "messages": messages,
    }
