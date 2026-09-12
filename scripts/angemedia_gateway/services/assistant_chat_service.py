"""Scoped AngeMedia assistant chat service."""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, AsyncIterator

from ..outbound_http import outbound_client
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
from .assistant_chat_context import prepare_assistant_context_async
from .assistant_config_service import resolve_assistant_runtime
from .assistant_knowledge_base import search_assistant_knowledge
from .assistant_skills import safe_tool_event, skill_event

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
GREETING_RE = re.compile(r"^(hi|hello|hey|你好|您好|嗨|在吗|在不在|小助手|帮助|help)[\s!！。,.，?？]*$", re.I)

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
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def _natural_text(value: Any, *, limit: int = 4000) -> str:
    text = _safe_text(value, limit=limit)
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"[*`#>]+", "", text)
    text = re.sub(r"(?m)^\s*\|?[\s:|-]{3,}\|?\s*$", "", text)
    text = text.replace("|", " / ")
    text = re.sub(r"(?m)^\s*[-*]\s*", "· ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def _language(message: str, requested: str | None = None) -> str:
    if requested in {"zh", "en"}:
        return requested
    return "zh" if CJK_RE.search(message or "") else "en"


def _in_scope(message: str) -> bool:
    lowered = message.lower()
    if _is_greeting(message):
        return True
    if len(message.strip()) <= 12 and any(term in lowered for term in ("你", "什么", "模型", "是谁", "用法", "介绍", "help", "hello", "hi")):
        return True
    return any(term.lower() in lowered for term in SCOPE_TERMS)


def _is_greeting(message: str) -> bool:
    return bool(GREETING_RE.match((message or "").strip()))


def _search_kb(message: str, *, limit: int = 4) -> list[dict[str, str]]:
    """Compatibility wrapper around the shared Assistant KB service."""
    return search_assistant_knowledge(message, limit=limit)


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


def _format_tool_answer(
    message: str,
    hits: list[dict[str, str]],
    language: str,
    tool_results: list[dict[str, Any]],
) -> str:
    useful = [item for item in tool_results if item.get("tool") != "local_knowledge_base"]
    if not useful:
        return _format_answer(message, hits, language)
    lines: list[str] = []
    for item in useful:
        tool = str(item.get("tool") or "")
        status = str(item.get("status") or "")
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        if status != "done":
            summary = _safe_text(item.get("summary"), limit=240)
            lines.append((f"{tool}：{summary}" if language == "zh" else f"{tool}: {summary}"))
            continue
        if tool == "job_safe_summary":
            if language == "zh":
                line = f"任务 {data.get('job_id')} 当前是 {data.get('status')}，阶段 {data.get('stage')}。"
                if data.get("error_code"):
                    line += f" 错误码 {data.get('error_code')}。"
                if data.get("asset_count"):
                    line += f" 已记录 {data.get('asset_count')} 个资产。"
            else:
                line = f"Job {data.get('job_id')} is {data.get('status')} at stage {data.get('stage')}."
                if data.get("error_code"):
                    line += f" Error code: {data.get('error_code')}."
            lines.append(line)
        elif tool == "failure_diagnostic":
            hint = data.get("human_hint")
            category = data.get("error_category") or data.get("error_code") or "unclassified"
            if language == "zh":
                lines.append(f"失败分类：{category}。" + (f" {hint}" if hint else ""))
            else:
                lines.append(f"Failure category: {category}." + (f" {hint}" if hint else ""))
        elif tool in {"diagnostics_summary", "queue_status"}:
            queue = data.get("queue") if isinstance(data.get("queue"), dict) else data.get("queue", {})
            if tool == "diagnostics_summary":
                queue = data.get("queue") if isinstance(data.get("queue"), dict) else {}
                database = data.get("database") if isinstance(data.get("database"), dict) else {}
                if language == "zh":
                    lines.append(
                        f"运行诊断：队列后端 {queue.get('backend') or 'unknown'}，健康={bool(queue.get('healthy'))}；"
                        f"数据库可达={bool(database.get('reachable'))}。"
                    )
                else:
                    lines.append(
                        f"Runtime diagnostics: queue backend {queue.get('backend') or 'unknown'}, healthy={bool(queue.get('healthy'))}; "
                        f"database reachable={bool(database.get('reachable'))}."
                    )
            else:
                if language == "zh":
                    lines.append(
                        f"队列状态：后端 {queue.get('backend') or 'unknown'}，健康={bool(queue.get('healthy'))}，"
                        f"活动任务 {int(queue.get('active_total') or 0)}。"
                    )
                else:
                    lines.append(
                        f"Queue status: backend {queue.get('backend') or 'unknown'}, healthy={bool(queue.get('healthy'))}, "
                        f"active jobs={int(queue.get('active_total') or 0)}."
                    )
        elif tool == "channel_safe_summary":
            channels = data.get("channels") if isinstance(data.get("channels"), list) else []
            if len(channels) == 1:
                channel = channels[0]
                if language == "zh":
                    lines.append(
                        f"渠道 {channel.get('provider_id')}：启用={bool(channel.get('enabled'))}，"
                        f"密钥已配置={bool(channel.get('key_configured'))}，默认模型={channel.get('default_model') or '未声明'}。"
                    )
                else:
                    lines.append(
                        f"Channel {channel.get('provider_id')}: enabled={bool(channel.get('enabled'))}, "
                        f"key configured={bool(channel.get('key_configured'))}, default model={channel.get('default_model') or 'not declared'}."
                    )
            else:
                lines.append((f"已读取 {len(channels)} 个渠道的安全摘要。" if language == "zh" else f"Read {len(channels)} safe channel summaries."))
        elif tool == "catalog_model_capabilities":
            caps = data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {}
            supported = [name for name, enabled in caps.items() if enabled]
            if language == "zh":
                lines.append(f"模型 {data.get('model_id')} 已声明能力：{', '.join(supported) if supported else '无'}。")
            else:
                lines.append(f"Model {data.get('model_id')} declares: {', '.join(supported) if supported else 'none'}.")
        else:
            lines.append(_safe_text(item.get("summary"), limit=240))
    if hits:
        first = _safe_text(hits[0].get("summary"), limit=500)
        if first:
            lines.append((f"本地排障资料补充：{first}" if language == "zh" else f"Bundled runbook note: {first}"))
    return "\n".join(line for line in lines if line).strip()


def _refusal(language: str) -> str:
    if language == "zh":
        return "我只能回答 AngeMedia Gateway / Studio / 队列 / 生成 / 渠道 / 资产 / 诊断相关问题。这个问题超出范围。"
    return "I can only answer AngeMedia Gateway, Studio, queue, generation, channel, asset, and diagnostics questions. This request is out of scope."


def _greeting_answer(language: str) -> str:
    if language == "zh":
        return (
            "你好。我可以帮你看 AngeMedia 的生成任务、失败诊断、渠道配置、资产结果、队列状态和提示词设置。"
            "你可以直接问：图片失败怎么查、视频超时怎么办、Agnes 渠道如何配置，或贴一个任务 ID。"
        )
    return (
        "Hi. I can help with AngeMedia generation jobs, failure diagnostics, channel configuration, assets, queue status, "
        "and prompt settings. Ask about a job id, a failed image/video run, or channel setup."
    )


def _event(language: str, tool: str, zh: str, en: str, *, status: str = "done") -> dict[str, str]:
    return safe_tool_event(tool, zh if language == "zh" else en, status=status)


def _llm_chat_configured() -> bool:
    runtime = resolve_assistant_runtime()
    return bool(assistant_enabled() and runtime.base_url and runtime.model)


def _chat_messages(
    message: str,
    hits: list[dict[str, str]],
    language: str,
    *,
    tool_results: list[dict[str, Any]] | None = None,
    skill_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    context = {
        "language": language,
        "question": message,
        "safe_kb_hits": hits,
        "safe_tool_results": list(tool_results or []),
        "selected_skill": dict(skill_context or {}),
        "allowed_scope": list(SCOPE_TERMS),
    }
    system = (
        "You are the scoped AngeMedia Studio assistant. Answer only AngeMedia Gateway / Studio / queue / "
        "generation / channels / assets / diagnostics / configuration questions. If the request is out of scope, "
        "refuse briefly. Use only the safe KB/tool context for operational facts and do not invent missing state. "
        "Tool results are read-only observations; never claim a mutation was performed. Never reveal API keys, "
        "Authorization headers, raw provider bodies, request hashes, signed URLs, data URLs, or local filesystem paths. "
        "For Chinese language requests, answer in Chinese. For English requests, answer in English. "
        "Use concise natural plain text. Do not use Markdown headings, Markdown tables, bold markers, code fences, or large code blocks unless the user explicitly asks. "
        "Prefer 2-5 short Chinese sentences for Chinese users. Keep operational steps numbered only when useful. "
        "When diagnosing failures, give concrete next checks and mention that Jobs detail and Diagnostics contain the safe evidence. "
        "For LLM setup, use the real Studio path: top Assistant entry, Settings, base URL, API key, model, Fetch Models/Test Connection, Save. "
        "Do not name example third-party models unless they appear in the provided safe context or user message."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": str(context)},
    ]


async def _call_llm_chat(
    message: str,
    hits: list[dict[str, str]],
    language: str,
    *,
    tool_results: list[dict[str, Any]] | None = None,
    skill_context: dict[str, Any] | None = None,
) -> tuple[str, int]:
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
    async with outbound_client(timeout=timeout) as client:
        resp = await client.post(
            f"{runtime.base_url}/chat/completions",
            headers=headers,
            json={
                "model": runtime.model,
                "temperature": temperature,
                "max_tokens": 900,
                "messages": _chat_messages(
                    message,
                    hits,
                    language,
                    tool_results=tool_results,
                    skill_context=skill_context,
                ),
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"assistant LLM failed: HTTP {resp.status_code}")
    data = resp.json()
    content = str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
    if not content:
        raise RuntimeError("assistant LLM returned empty content")
    return _natural_text(content, limit=4000), int((time.perf_counter() - started) * 1000)


async def _stream_llm_chat(
    message: str,
    hits: list[dict[str, str]],
    language: str,
    *,
    tool_results: list[dict[str, Any]] | None = None,
    skill_context: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
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
    async with outbound_client(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{runtime.base_url}/chat/completions",
            headers=headers,
            json={
                "model": runtime.model,
                "temperature": temperature,
                "max_tokens": 900,
                "stream": True,
                "messages": _chat_messages(
                    message,
                    hits,
                    language,
                    tool_results=tool_results,
                    skill_context=skill_context,
                ),
            },
        ) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(f"assistant LLM failed: HTTP {resp.status_code}")
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if not line or line == "[DONE]":
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                delta = data.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content")
                if content:
                    yield str(content)


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _prepare_tool_context(
    message: str,
    timeline: list[dict[str, str]],
) -> tuple[str, list[dict[str, str]], list[dict[str, Any]], dict[str, Any]]:
    prepared = await prepare_assistant_context_async(message)
    timeline.append(skill_event(prepared.skill))
    tool_results = prepared.safe_tool_results()
    for result in tool_results:
        event_name = "local_kb_search" if result.get("tool") == "local_knowledge_base" else str(result.get("tool") or "assistant_tool")
        timeline.append(
            safe_tool_event(
                event_name,
                str(result.get("summary") or ""),
                status=str(result.get("status") or "done"),
            )
        )
    skill_context = {
        **prepared.skill.summary(),
        "instructions": _safe_text(prepared.skill.body, limit=2000),
    }
    return (
        prepared.skill.id,
        list(prepared.kb_hits),
        tool_results,
        skill_context,
    )


async def build_assistant_chat_stream(payload: dict[str, Any]) -> AsyncIterator[str]:
    message = _safe_text(payload.get("message"), limit=4000)
    if not message:
        yield sse_event("error", {"message": "message is required"})
        return
    language = _language(message, payload.get("language"))
    session_id = _safe_text(payload.get("session_id"), limit=64)
    session = get_assistant_session(session_id) if session_id else None
    if not session:
        session_id = uuid.uuid4().hex
        session = create_assistant_session(session_id, message[:80] or "AngeMedia Assistant")

    add_assistant_message(uuid.uuid4().hex, session_id, "user", message)
    timeline = [_event(language, "scope_guard", "已检查 AngeMedia 专用助手范围", "checked AngeMedia-only assistant scope")]
    status = "succeeded"
    skill_id = "angemedia_faq"
    tool_results: list[dict[str, Any]] = []
    skill_context: dict[str, Any] = {}
    yield sse_event("status", {"status": "accepted", "session_id": session_id})

    try:
        if _is_greeting(message):
            answer = _natural_text(_greeting_answer(language))
            hits: list[dict[str, str]] = []
            timeline.append(_event(language, "scope_guard", "问候已放行，返回 AngeMedia 使用引导", "greeting accepted with AngeMedia guidance"))
            yield sse_event("chunk", {"content": answer})
        elif not _in_scope(message):
            answer = _natural_text(_refusal(language))
            hits = []
            status = "refused"
            timeline.append(_event(language, "scope_guard", "问题超出 AngeMedia 范围，已拒绝", "request refused as out of scope", status="refused"))
            yield sse_event("chunk", {"content": answer})
        else:
            skill_id, hits, tool_results, skill_context = await _prepare_tool_context(message, timeline)
            if _llm_chat_configured():
                started = time.perf_counter()
                raw_answer = ""
                emitted = ""
                try:
                    async for token in _stream_llm_chat(
                        message,
                        hits,
                        language,
                        tool_results=tool_results,
                        skill_context=skill_context,
                    ):
                        raw_answer += token
                        clean = _natural_text(raw_answer, limit=4000)
                        if clean.startswith(emitted):
                            delta = clean[len(emitted):]
                            if delta:
                                emitted = clean
                                yield sse_event("chunk", {"content": delta})
                    answer = _natural_text(raw_answer, limit=4000)
                    if not answer:
                        raise RuntimeError("assistant LLM returned empty content")
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    timeline.append(_event(language, "llm_chat", f"已使用安全工具上下文调用已配置 LLM，耗时 {elapsed_ms}ms", f"answered with configured LLM and safe tool context in {elapsed_ms}ms"))
                except Exception as exc:
                    if raw_answer:
                        answer = _natural_text(raw_answer, limit=4000)
                        timeline.append(_event(language, "llm_chat", f"LLM 流式响应已返回部分内容，随后中断：{redact_secret_text(str(exc))}", f"LLM stream returned partial content then stopped: {redact_secret_text(str(exc))}", status="partial"))
                    else:
                        timeline.append(_event(language, "llm_chat", f"LLM 调用失败，已回退安全工具/本地知识：{redact_secret_text(str(exc))}", f"LLM failed; used safe tool/KB fallback: {redact_secret_text(str(exc))}", status="fallback"))
                        answer = _natural_text(_format_tool_answer(message, hits, language, tool_results))
                        yield sse_event("chunk", {"content": answer})
            else:
                timeline.append(_event(language, "llm_chat", "LLM 未启用或未配置，已使用安全工具/本地知识回退", "LLM disabled or not configured; used safe tool/KB fallback", status="skipped"))
                answer = _natural_text(_format_tool_answer(message, hits, language, tool_results))
                yield sse_event("chunk", {"content": answer})
    except Exception as exc:
        yield sse_event("error", {"message": redact_secret_text(str(exc))[:240]})
        return

    assistant_message = add_assistant_message(
        uuid.uuid4().hex,
        session_id,
        "assistant",
        answer,
        {"kb_hits": hits, "tool_results": tool_results, "status": status},
    )
    run = add_assistant_run(
        uuid.uuid4().hex,
        session_id,
        status,
        skill_id,
        {"message": message, "language": language},
        {"answer": answer, "kb_hits": hits, "tool_results": tool_results},
        timeline,
    )
    yield sse_event("timeline", {"items": timeline})
    yield sse_event("done", {"session_id": session_id, "status": status, "message_id": assistant_message.get("id"), "run_id": run.get("id")})


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
    tool_results: list[dict[str, Any]] = []
    skill_context: dict[str, Any] = {}
    if _is_greeting(message):
        answer = _natural_text(_greeting_answer(language))
        hits = []
        timeline.append(_event(language, "scope_guard", "问候已放行，返回 AngeMedia 使用引导", "greeting accepted with AngeMedia guidance"))
    elif not _in_scope(message):
        answer = _natural_text(_refusal(language))
        hits: list[dict[str, str]] = []
        status = "refused"
        timeline.append(_event(language, "scope_guard", "问题超出 AngeMedia 范围，已拒绝", "request refused as out of scope", status="refused"))
    else:
        skill_id, hits, tool_results, skill_context = await _prepare_tool_context(message, timeline)
        if _llm_chat_configured():
            try:
                answer, elapsed_ms = await _call_llm_chat(
                    message,
                    hits,
                    language,
                    tool_results=tool_results,
                    skill_context=skill_context,
                )
                timeline.append(_event(language, "llm_chat", f"已使用安全工具上下文调用已配置 LLM，耗时 {elapsed_ms}ms", f"answered with configured LLM and safe tool context in {elapsed_ms}ms"))
            except Exception as exc:
                timeline.append(_event(language, "llm_chat", f"LLM 调用失败，已回退安全工具/本地知识：{redact_secret_text(str(exc))}", f"LLM failed; used safe tool/KB fallback: {redact_secret_text(str(exc))}", status="fallback"))
                answer = _natural_text(_format_tool_answer(message, hits, language, tool_results))
        else:
            timeline.append(_event(language, "llm_chat", "LLM 未启用或未配置，已使用安全工具/本地知识回退", "LLM disabled or not configured; used safe tool/KB fallback", status="skipped"))
            answer = _natural_text(_format_tool_answer(message, hits, language, tool_results))

    assistant_message = add_assistant_message(
        uuid.uuid4().hex,
        session_id,
        "assistant",
        answer,
        {"kb_hits": hits, "tool_results": tool_results, "status": status},
    )
    run = add_assistant_run(
        uuid.uuid4().hex,
        session_id,
        status,
        skill_id,
        {"message": message, "language": language},
        {"answer": answer, "kb_hits": hits, "tool_results": tool_results},
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
