"""旧客户端本地帮助兼容层；不连接模型，正式对话统一由运行服务执行。"""
from __future__ import annotations

import re
import uuid
from typing import Any

from ..assistant_sanitizer import sanitize_assistant_text as _safe_text
from ..repositories.assistant_sessions import (
    add_assistant_message,
    add_assistant_run,
    create_assistant_session,
    get_assistant_session,
    list_assistant_messages,
)
from .assistant_chat_context import prepare_assistant_context_async
from .assistant_knowledge_base import search_assistant_knowledge
from .assistant_skills import safe_tool_event, skill_event


CJK_RE = re.compile(r"[\u4e00-\u9fff]")
GREETING_RE = re.compile(r"^(hi|hello|hey|你好|您好|嗨|在吗|在不在|小助手|帮助|help)[\s!！。,.，?？]*$", re.I)


def _natural_text(value: Any, *, limit: int = 4000) -> str:
    text = _safe_text(value, limit=limit)
    text = re.sub(r"```[A-Za-z0-9_+-]*[ \t]*(?:\n|$)", "", text).replace("```", "")
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


def _is_greeting(message: str) -> bool:
    return bool(GREETING_RE.match((message or "").strip()))


def _search_kb(message: str, *, limit: int = 4) -> list[dict[str, str]]:
    """Compatibility wrapper around the shared Assistant KB service."""
    return search_assistant_knowledge(message, limit=limit)


def _format_answer(message: str, hits: list[dict[str, str]], language: str) -> str:
    if not hits:
        return (
            "当前只能提供有限的本地资料，尚不能确定你想完成的具体目标；"
            "你可以补充具体页面、任务 ID、渠道或错误信息。"
            if language == "zh"
            else "Only limited local knowledge is available. Please clarify your goal, or share the page, job id, channel, or error."
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
        elif tool == "recent_logs":
            sources = data.get("sources") if isinstance(data.get("sources"), list) else []
            emitted = 0
            for source in sources:
                if not isinstance(source, dict) or not source.get("available"):
                    continue
                name = _safe_text(source.get("source"), limit=32) or "log"
                entries = source.get("lines") if isinstance(source.get("lines"), list) else []
                selected = entries[-5:]
                if not selected:
                    continue
                lines.append((f"{name} 最近日志：" if language == "zh" else f"Recent {name} log:"))
                for entry in selected:
                    lines.append(f"- {_safe_text(entry, limit=420)}")
                    emitted += 1
                    if emitted >= 12:
                        break
                if emitted >= 12:
                    break
            if emitted == 0:
                lines.append(("当前没有可读取的 AngeMedia 文件日志。" if language == "zh" else "No readable AngeMedia file logs are currently available."))
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


async def build_assistant_chat_reply(payload: dict[str, Any]) -> dict[str, Any]:
    message = _safe_text(payload.get("message"), limit=4000)
    if not message:
        raise ValueError("message is required")
    language = _language(message, payload.get("language"))
    session_id = _safe_text(payload.get("session_id"), limit=64)
    session = get_assistant_session(session_id, reject_private=True) if session_id else None
    if not session:
        session_id = uuid.uuid4().hex
        session = create_assistant_session(session_id, message[:80] or "AngeMedia Assistant")

    user_message = add_assistant_message(uuid.uuid4().hex, session_id, "user", message)
    timeline = [_event(language, "scope_guard", "已准备专精助手上下文，自然问法不按关键词拒答", "prepared specialized context without keyword rejection")]
    status = "succeeded"
    skill_id = "angemedia_faq"
    tool_results: list[dict[str, Any]] = []
    skill_context: dict[str, Any] = {}
    if _is_greeting(message):
        answer = _natural_text(_greeting_answer(language))
        hits = []
        timeline.append(_event(language, "scope_guard", "问候已放行，返回 AngeMedia 使用引导", "greeting accepted with AngeMedia guidance"))
    else:
        skill_id, hits, tool_results, skill_context = await _prepare_tool_context(message, timeline)
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
