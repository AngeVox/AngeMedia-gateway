"""Assistant-backed prompt copilot with safe local fallback."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx

from ..assistant import assistant_enabled, parse_llm_json_content
from ..repositories.settings import get_config
from ..routing import infer_media_type
from ..schemas import EnhanceRequest
from ..security import redact_secret_text
from .assistant_config_service import resolve_assistant_runtime
from .assistant_skills import AssistantSkill, safe_tool_event, select_prompt_skill, skill_event
from .prompt_enhancer import enhance_prompt

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


class PromptCopilotError(Exception):
    """Raised when the LLM prompt copilot path cannot be used."""


def _display_language(req: EnhanceRequest, source_prompt: str) -> str:
    if req.language in {"zh", "en"}:
        return req.language
    return "zh" if CJK_RE.search(source_prompt or "") else "en"


def _safe_list(value: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = redact_secret_text(str(item or ""))
        text = " ".join(text.split())
        if text:
            result.append(text[:240])
        if len(result) >= limit:
            break
    return result


def _safe_text(value: Any, *, limit: int = 1200) -> str:
    text = redact_secret_text(str(value or ""))
    return " ".join(text.split())[:limit]


def _llm_configured() -> bool:
    runtime = resolve_assistant_runtime()
    return bool(runtime.base_url and runtime.model and runtime.api_key)


def _normalize_llm_result(raw: dict[str, Any], fallback: dict[str, Any], req: EnhanceRequest) -> dict[str, Any]:
    mode = str(raw.get("mode") or fallback.get("mode") or "expand")
    if mode not in {"expand", "polish", "translate", "no_change"}:
        mode = str(fallback.get("mode") or "expand")
    model_prompt = _safe_text(raw.get("model_prompt_en") or raw.get("english_prompt") or raw.get("prompt"), limit=4000)
    if not model_prompt or CJK_RE.search(model_prompt):
        model_prompt = str(fallback.get("model_prompt_en") or "")
    display_zh = _safe_text(raw.get("user_display_prompt_zh") or raw.get("zh_preview") or fallback.get("user_display_prompt_zh"))
    user_display = display_zh if _display_language(req, req.prompt) == "zh" else _safe_text(
        raw.get("user_display_prompt") or raw.get("display_prompt") or fallback.get("user_display_prompt"),
    )
    notes_zh = _safe_list(raw.get("notes_zh") or raw.get("notes"), limit=6) or list(fallback.get("notes_zh") or [])
    notes = notes_zh if _display_language(req, req.prompt) == "zh" else _safe_list(raw.get("notes"), limit=6) or list(fallback.get("notes") or [])
    return {
        **fallback,
        "mode": mode,
        "changed": True,
        "user_display_prompt": user_display,
        "user_display_prompt_zh": display_zh,
        "model_prompt_en": model_prompt,
        "negative_prompt": _safe_text(raw.get("negative_prompt"), limit=1000) or fallback.get("negative_prompt"),
        "notes": notes,
        "notes_zh": notes_zh,
        "warnings": _safe_list(raw.get("warnings"), limit=6),
    }


def _messages(req: EnhanceRequest, skill: AssistantSkill, fallback: dict[str, Any]) -> list[dict[str, str]]:
    payload = {
        "prompt": req.prompt,
        "media_type": fallback.get("input_summary", {}).get("media_type") or req.media_type,
        "display_language": _display_language(req, req.prompt),
        "target_prompt_language": "en",
        "skill": skill.summary(),
        "local_fallback_prompt_en": fallback.get("model_prompt_en"),
    }
    system = (
        "You are AngeMedia Prompt Copilot. Return JSON only. "
        "Never submit jobs. The model_prompt_en field must be English. "
        "Do not include secrets, raw provider payloads, request hashes, signed URLs, data URLs, or local paths.\n\n"
        f"Skill:\n{skill.body}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


async def call_llm_for_prompt_copilot(req: EnhanceRequest, skill: AssistantSkill, fallback: dict[str, Any]) -> dict[str, Any]:
    runtime = resolve_assistant_runtime()
    if not runtime.base_url or not runtime.model or not runtime.api_key:
        raise PromptCopilotError("LLM prompt copilot is not configured")
    try:
        timeout = float(get_config("ANGE_LLM_TIMEOUT", os.getenv("ANGE_LLM_TIMEOUT", "60")))
        temperature = float(get_config("ANGE_LLM_TEMPERATURE", os.getenv("ANGE_LLM_TEMPERATURE", "0.35")))
    except ValueError:
        timeout, temperature = 60.0, 0.35
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {runtime.api_key}"}
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{runtime.base_url}/chat/completions",
            headers=headers,
            json={
                "model": runtime.model,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
                "messages": _messages(req, skill, fallback),
            },
        )
    if resp.status_code >= 400:
        raise PromptCopilotError(f"LLM prompt copilot failed: HTTP {resp.status_code}")
    try:
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = parse_llm_json_content(content)
    except Exception as exc:
        raise PromptCopilotError("LLM prompt copilot returned invalid JSON") from exc
    parsed["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return parsed


def _with_common_shape(result: dict[str, Any], req: EnhanceRequest, skill: AssistantSkill, timeline: list[dict[str, str]]) -> dict[str, Any]:
    input_summary = dict(result.get("input_summary") or {})
    input_summary["display_language"] = _display_language(req, req.prompt)
    input_summary["media_type"] = input_summary.get("media_type") or infer_media_type(req.prompt, req.media_type)
    return {
        **result,
        "input_summary": input_summary,
        "skill": skill.summary(),
        "timeline": timeline,
    }


async def build_prompt_copilot(req: EnhanceRequest) -> dict[str, Any]:
    local = enhance_prompt(req)
    media_type = str(local.get("input_summary", {}).get("media_type") or infer_media_type(req.prompt, req.media_type))
    skill = select_prompt_skill(media_type)
    timeline = [
        skill_event(skill),
        safe_tool_event("local_prompt_enhancer", f"prepared safe fallback for {media_type}"),
    ]
    if not assistant_enabled() or not _llm_configured():
        local["assistant_status"] = {"mode": "local_fallback", "llm_used": False}
        return _with_common_shape(local, req, skill, timeline)
    try:
        raw = await call_llm_for_prompt_copilot(req, skill, local)
        llm_result = _normalize_llm_result(raw, local, req)
        elapsed = raw.get("elapsed_ms")
        summary = f"LLM generated prompt suggestion"
        if elapsed is not None:
            summary = f"{summary} in {int(elapsed)}ms"
        timeline.append(safe_tool_event("llm_prompt_copilot", summary))
        llm_result["assistant_status"] = {"mode": "llm", "llm_used": True}
        return _with_common_shape(llm_result, req, skill, timeline)
    except Exception as exc:
        timeline.append(safe_tool_event("llm_prompt_copilot", redact_secret_text(str(exc)), status="fallback"))
        local["assistant_status"] = {"mode": "llm_fallback", "llm_used": False}
        return _with_common_shape(local, req, skill, timeline)
