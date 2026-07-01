"""Scoped AngeMedia assistant chat service."""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from .. import config as C
from ..security import redact_secret_text
from ..repositories.assistant_sessions import (
    add_assistant_message,
    add_assistant_run,
    create_assistant_session,
    get_assistant_session,
    list_assistant_messages,
)
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


def build_assistant_chat_reply(payload: dict[str, Any]) -> dict[str, Any]:
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
    timeline = [safe_tool_event("scope_guard", "checked AngeMedia-only assistant scope")]
    status = "succeeded"
    skill_id = "angemedia_faq"
    if not _in_scope(message):
        answer = _refusal(language)
        hits: list[dict[str, str]] = []
        status = "refused"
        timeline.append(safe_tool_event("scope_guard", "request refused as out of scope", status="refused"))
    else:
        hits = _search_kb(message)
        timeline.append(safe_tool_event("local_kb_search", f"found {len(hits)} safe KB hit(s)"))
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
