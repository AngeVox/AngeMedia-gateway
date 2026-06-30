"""Local assistant planning service.

This planner is intentionally recommendation-only. It reuses the prompt
enhancer and route heuristics, but never submits jobs or calls providers.
"""
from __future__ import annotations

import uuid
import os
from typing import Any

from ..assistant import assistant_enabled
from ..assistant import build_assistant_plan as build_formal_assistant_plan
from ..providers.errors import BackendUnavailable
from ..repositories.assistant_plans import save_assistant_plan
from ..repositories.settings import get_config
from ..routing import build_route_response
from ..schemas import AssistantRequest, EnhanceRequest, RouteRequest
from ..services.prompt_enhancer import _sanitize_text, enhance_prompt

ALLOWED_CONTEXT_KEYS = {"current_page", "current_prompt", "selected_model"}


def _display_language(req: AssistantRequest) -> str:
    if req.language in {"zh", "en"}:
        return req.language
    return "zh" if any("\u4e00" <= char <= "\u9fff" for char in str(req.message or "")) else "en"


def _safe_context(context: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(context, dict):
        return {}
    safe: dict[str, str] = {}
    for key in ALLOWED_CONTEXT_KEYS:
        value = context.get(key)
        if value is None:
            continue
        safe[key] = _sanitize_text(str(value))[:500]
    return safe


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in str(value or ""))


def _ensure_english_model_prompt(value: str, *, media_type: str, language: str) -> str:
    clean = _sanitize_text(str(value or ""))
    if clean and not _contains_cjk(clean):
        return clean
    enhanced = enhance_prompt(
        EnhanceRequest(
            prompt=clean or "the subject described by the user",
            media_type=media_type,
            language=language,
            target_language="en",
            strength="auto",
        )
    )
    return str(enhanced["model_prompt_en"])


def _assistant_message(media_type: str, language: str) -> str:
    if language == "zh":
        label = "图片" if media_type == "image" else "视频"
        return f"我建议先准备一个{label}生成计划，并保留为需要你确认的建议。"
    label = "image" if media_type == "image" else "video"
    return f"I recommend a {label} plan. Review it before applying the English model prompt."


def _route_reason(media_type: str, language: str) -> tuple[str, str]:
    if media_type == "video":
        reason_zh = "用户需求包含视频、运动或镜头变化，适合进入视频生成页面。"
        reason_en = "The request mentions video, motion, or shot changes, so it fits video generation."
    else:
        reason_zh = "用户描述的是静态画面或视觉主体，适合进入图片生成页面。"
        reason_en = "The request describes a static visual subject, so it fits image generation."
    return reason_zh, reason_zh if language == "zh" else reason_en


def _work_steps(media_type: str, language: str) -> list[str]:
    if language == "zh":
        target = "图片" if media_type == "image" else "视频"
        return [
            "确认推荐计划和英文模型提示词",
            f"应用到{target}生成页面或继续编辑",
            "用户手动点击生成后才进入队列",
        ]
    target = "image" if media_type == "image" else "video"
    return [
        "Review the recommendation and English model prompt",
        f"Apply it to the {target} page or keep editing",
        "Only a later user submit creates a queued job",
    ]


def _actions(media_type: str, language: str) -> list[dict[str, Any]]:
    target_page = "generate-video" if media_type == "video" else "generate-image"
    action_id = "apply_to_video" if media_type == "video" else "apply_to_image"
    label_zh = "应用到视频生成" if media_type == "video" else "应用到图片生成"
    label_en = "Apply to Video" if media_type == "video" else "Apply to Image"
    return [
        {
            "id": action_id,
            "label": label_zh if language == "zh" else label_en,
            "label_zh": label_zh,
            "target_page": target_page,
        },
        {
            "id": "copy_english_prompt",
            "label": "复制英文提示词" if language == "zh" else "Copy English Prompt",
            "label_zh": "复制英文提示词",
            "target_page": None,
        },
    ]


def _assistant_status() -> dict[str, Any]:
    llm_enabled = assistant_enabled()
    llm_configured = bool(get_config("ANGE_LLM_API_KEY", os.getenv("ANGE_LLM_API_KEY", "")).strip())
    mode = "disabled" if not llm_enabled else ("configured" if llm_configured else "local_fallback")
    return {
        "mode": mode,
        "planner": "local_recommendation",
        "llm_enabled": llm_enabled,
        "llm_configured": llm_configured,
        "llm_used": False,
    }


def _assistant_status_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    status = _assistant_status()
    llm_used = plan.get("assistant_mode") == "llm"
    status.update(
        {
            "mode": "llm" if llm_used else "local_fallback",
            "planner": "llm_recommendation" if llm_used else "local_recommendation",
            "llm_used": llm_used,
        }
    )
    return status


def _suggested_params(media_type: str, route: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "size": route.get("size"),
        "aspect_ratio": route.get("aspect_ratio"),
        "duration": None,
    }
    if media_type == "video":
        params.update(
            {
                "width": route.get("width"),
                "height": route.get("height"),
                "num_frames": route.get("num_frames"),
                "frame_rate": route.get("frame_rate"),
                "input_mode": route.get("input_mode"),
            }
        )
    return params


def build_local_assistant_plan(req: AssistantRequest) -> dict[str, Any]:
    """Build a deterministic, safe assistant recommendation."""

    message = _sanitize_text(str(req.message or req.prompt or ""))[:4000]
    language = _display_language(req)
    context = _safe_context(req.context)
    selected_model = context.get("selected_model") or None
    route_req = RouteRequest(
        prompt=message,
        media_type=req.media_type,
        images=req.images,
        requested_model=selected_model,
        size=req.size,
    )
    route = build_route_response(route_req)
    media_type = route["media_type"]
    enhanced = enhance_prompt(
        EnhanceRequest(
            prompt=message,
            media_type=media_type,
            language=language,
            target_language=req.target_prompt_language,
            strength="auto",
        )
    )
    target_page = "generate-video" if media_type == "video" else "generate-image"
    reason_zh, reason = _route_reason(media_type, language)
    plan_id = uuid.uuid4().hex
    plan: dict[str, Any] = {
        "plan_id": plan_id,
        "assistant_message": _assistant_message(media_type, language),
        "mode": "local_recommendation",
        "assistant_status": _assistant_status(),
        "requires_user_confirmation": True,
        "media_type": media_type,
        "route": {
            "target_page": target_page,
            "provider": route.get("provider"),
            "model": route.get("model"),
            "reason": reason,
            "reason_zh": reason_zh,
        },
        "prompt": {
            "user_display_prompt": enhanced["user_display_prompt"],
            "user_display_prompt_zh": enhanced["user_display_prompt_zh"],
            "model_prompt_en": enhanced["model_prompt_en"],
            "negative_prompt": enhanced.get("negative_prompt"),
            "mode": enhanced["mode"],
        },
        "suggested_params": _suggested_params(media_type, route),
        "work_steps": _work_steps(media_type, language),
        "actions": _actions(media_type, language),
        "warnings": list(enhanced.get("warnings") or []),
        "input_summary": {
            "media_type": media_type,
            "language": language,
            "target_prompt_language": req.target_prompt_language,
            "context": context,
            "llm_required": False,
        },
    }
    save_assistant_plan(plan_id, message, media_type, plan)
    return plan


async def build_assistant_recommendation(req: AssistantRequest) -> dict[str, Any]:
    """Build the Studio-facing assistant plan shape from the formal planner."""

    if not assistant_enabled() or not get_config("ANGE_LLM_API_KEY", os.getenv("ANGE_LLM_API_KEY", "")).strip():
        return build_local_assistant_plan(req)

    try:
        plan = await build_formal_assistant_plan(req)
    except BackendUnavailable:
        fallback = build_local_assistant_plan(req)
        fallback["assistant_status"] = {
            **fallback.get("assistant_status", {}),
            "mode": "config_error",
            "planner": "local_recommendation",
            "llm_enabled": True,
            "llm_configured": True,
            "llm_used": False,
        }
        fallback["warnings"] = [*list(fallback.get("warnings") or []), "LLM 调用失败，已回退到本地建议。"]
        return fallback
    media_type = plan.get("media_type") if plan.get("media_type") in {"image", "video"} else "image"
    language = _display_language(req)
    target_page = "generate-video" if media_type == "video" else "generate-image"
    route_reason = plan.get("reason") or plan.get("notes")
    reason_zh, fallback_reason = _route_reason(media_type, language)
    raw_model_prompt = _sanitize_text(str(plan.get("prompt") or req.prompt or ""))
    model_prompt = _ensure_english_model_prompt(raw_model_prompt, media_type=media_type, language=language)
    display_prompt = model_prompt
    suggested_params = {
        "size": plan.get("size"),
        "aspect_ratio": plan.get("aspect_ratio"),
        "duration": None,
    }
    if media_type == "video":
        suggested_params.update(
            {
                "width": plan.get("width"),
                "height": plan.get("height"),
                "num_frames": plan.get("num_frames"),
                "frame_rate": plan.get("frame_rate"),
                "input_mode": plan.get("input_mode"),
            }
        )
    mode = "llm_recommendation" if plan.get("assistant_mode") == "llm" else "local_recommendation"
    return {
        "plan_id": plan.get("plan_id"),
        "assistant_message": _sanitize_text(str(plan.get("assistant_message") or _assistant_message(media_type, language)))[:1000],
        "mode": mode,
        "assistant_status": _assistant_status_from_plan(plan),
        "requires_user_confirmation": True,
        "media_type": media_type,
        "route": {
            "target_page": target_page,
            "provider": plan.get("provider"),
            "model": plan.get("model"),
            "reason": _sanitize_text(str(route_reason or fallback_reason))[:1000],
            "reason_zh": reason_zh,
        },
        "prompt": {
            "user_display_prompt": display_prompt,
            "user_display_prompt_zh": raw_model_prompt if language == "zh" and raw_model_prompt else "",
            "model_prompt_en": model_prompt,
            "negative_prompt": _sanitize_text(str(plan.get("negative_prompt") or ""))[:1000] or None,
            "mode": mode,
        },
        "suggested_params": suggested_params,
        "work_steps": [
            _sanitize_text(str(item))[:300]
            for item in (plan.get("work_steps") if isinstance(plan.get("work_steps"), list) else _work_steps(media_type, language))
            if item
        ][:8],
        "actions": _actions(media_type, language),
        "warnings": [],
        "input_summary": {
            "media_type": media_type,
            "language": language,
            "target_prompt_language": req.target_prompt_language,
            "llm_required": False,
        },
    }
