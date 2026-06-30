"""Ange 小助手规划逻辑。"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import HTTPException

from . import config as C
from .providers.errors import BackendUnavailable
from .providers.parsers import parse_size
from .routing import (
    MODEL_ALIASES,
    build_route_response,
    choose_default_size,
    choose_image_model,
    enhance_prompt_text,
    infer_media_type,
    infer_video_input_mode,
)
from .schemas import AssistantRequest, EnhanceRequest, RouteRequest
from .repositories.assistant_plans import save_assistant_plan
from .repositories.settings import get_config


def assistant_enabled() -> bool:
    return get_config("ANGE_ASSISTANT_ENABLED", os.getenv("ANGE_ASSISTANT_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}


def assistant_allow_paid() -> bool:
    return get_config("ANGE_ASSISTANT_ALLOW_PAID", os.getenv("ANGE_ASSISTANT_ALLOW_PAID", "false")).lower() in {"1", "true", "yes", "on"}


def assistant_allow_agnes() -> bool:
    return get_config("ANGE_ASSISTANT_ALLOW_AGNES", os.getenv("ANGE_ASSISTANT_ALLOW_AGNES", "true")).lower() in {"1", "true", "yes", "on"}


def load_assistant_system_prompt() -> str:
    prompt_path = C.PROJECT_ROOT / "docs" / "ANGE_ASSISTANT_SYSTEM_PROMPT.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "你是 Ange，AngeMedia Gateway 的媒体生成规划助手。你只能输出 JSON。"


def normalize_llm_plan(raw_plan: dict[str, Any]) -> dict[str, Any]:
    """兼容不同 LLM 返回结构，把核心媒体计划拍平成可执行字段。"""
    plan = dict(raw_plan or {})
    if isinstance(plan.get("plan"), dict):
        inner = dict(plan["plan"])
        for key, value in plan.items():
            if key != "plan" and key not in inner:
                inner[key] = value
        plan = inner

    media = plan.get("media")
    if isinstance(media, dict):
        media_type = media.get("type") or media.get("media_type")
        if media_type and "media_type" not in plan:
            plan["media_type"] = media_type
        content = media.get("content") or media.get("prompt")
        if content:
            plan["prompt"] = content
        for key in ("size", "width", "height", "num_frames", "frame_rate", "input_mode", "mode"):
            if key in media and key not in plan:
                plan[key] = media[key]

    if "content" in plan and "prompt" not in plan:
        plan["prompt"] = plan["content"]
    if "message" in plan and "assistant_message" not in plan:
        plan["assistant_message"] = plan["message"]
    return plan


def parse_llm_json_content(content: str) -> dict[str, Any]:
    """从兼容接口返回的文本中提取 JSON 对象，兼容 ```json 代码块。"""
    text = str(content or "").strip()
    if not text:
        raise ValueError("空内容")

    candidates = [text]
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    candidates = fenced + candidates
    decoder = json.JSONDecoder()
    for candidate in candidates:
        body = candidate.strip()
        if not body:
            continue
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        start = body.find("{")
        while start >= 0:
            try:
                data, _ = decoder.raw_decode(body[start:])
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                start = body.find("{", start + 1)
                continue
            break
    raise ValueError("未找到 JSON 对象")


def sanitize_assistant_plan(plan: dict[str, Any], req: AssistantRequest) -> dict[str, Any]:
    plan = normalize_llm_plan(plan)
    media_type = plan.get("media_type") if plan.get("media_type") in {"image", "video"} else infer_media_type(req.prompt, req.media_type)
    plan["media_type"] = media_type

    if media_type == "image":
        model = plan.get("model") or choose_image_model(req.prompt)
        if model in {"gpt-image-2", "openai-image"} and not assistant_allow_paid():
            model = None
        if isinstance(model, str) and model.startswith("agnes") and not assistant_allow_agnes():
            model = None
        if model and model.lower() not in MODEL_ALIASES:
            model = None
        plan["model"] = model
        size = str(plan.get("size") or req.size or choose_default_size(req.prompt, "image"))
        try:
            parse_size(size)
        except Exception:
            size = "1024x1024"
        plan["size"] = size
        plan["response_format"] = "url"
        plan["prompt"] = str(plan.get("prompt") or req.prompt).strip()[:32000]
        plan["prompt_changed"] = plan["prompt"] != req.prompt.strip()
        plan.setdefault("assistant_message", "我已理解你的图片需求，并整理成可直接生成的画面计划。")
        plan.setdefault("prompt_changes", ["补充主体和场景", "补充构图与光影", "加入画质与负面限制"] if plan["prompt_changed"] else ["保持原意，未强行改写"])
        plan.setdefault("work_steps", [
            "理解用户要生成图片",
            "补充主体、场景、构图与画质要求",
            "选择合适模型或交给默认链路",
        ])
        return plan

    plan["model"] = "agnes-video-v2.0"
    size = str(plan.get("size") or req.size or "1152x768")
    try:
        width, height = parse_size(size)
    except Exception:
        width, height, size = 1152, 768, "1152x768"
    plan["size"] = size
    plan["width"] = max(256, min(int(plan.get("width") or width), 2048))
    plan["height"] = max(256, min(int(plan.get("height") or height), 1536))
    frames = int(plan.get("num_frames") or 121)
    allowed = [81, 121, 161, 241, 441]
    if frames not in allowed:
        raise HTTPException(status_code=400, detail="Ange 小助手返回了非法 num_frames，只允许 81、121、161、241、441")
    plan["num_frames"] = frames
    plan["frame_rate"] = max(1, min(float(plan.get("frame_rate") or 24), 60))
    plan["wait_for_completion"] = bool(req.wait_for_completion or plan.get("wait_for_completion", False))
    plan["input_mode"] = plan.get("input_mode") or infer_video_input_mode(req.prompt, req.images)
    plan["prompt"] = str(plan.get("prompt") or req.prompt).strip()[:32000]
    plan["prompt_changed"] = plan["prompt"] != req.prompt.strip()
    plan.setdefault("assistant_message", "我已理解你的视频需求，并整理成包含镜头、运动和节奏的生成计划。")
    plan.setdefault("prompt_changes", ["补充镜头运动", "补充动作节奏", "补充画面连续性"] if plan["prompt_changed"] else ["保持原意，未强行改写"])
    plan.setdefault("work_steps", [
        "理解用户要生成视频",
        "判断首帧、参考图或纯文字输入模式",
        "补充镜头、运动、节奏与画面连续性要求",
    ])
    if req.images:
        if len(req.images) == 1:
            plan["image"] = req.images[0]
        else:
            plan["images"] = req.images
            if plan["input_mode"] == "first_last_frame":
                plan["mode"] = "keyframes"
    return plan


async def call_llm_for_plan(req: AssistantRequest) -> Optional[dict[str, Any]]:
    if not assistant_enabled():
        return None
    api_key = get_config("ANGE_LLM_API_KEY", os.getenv("ANGE_LLM_API_KEY", "")).strip()
    base_url = get_config("ANGE_LLM_BASE_URL", os.getenv("ANGE_LLM_BASE_URL", "")).rstrip("/")
    model = get_config("ANGE_LLM_MODEL", os.getenv("ANGE_LLM_MODEL", "")).strip()
    if not api_key or not base_url or not model:
        return None
    try:
        timeout = float(get_config("ANGE_LLM_TIMEOUT", os.getenv("ANGE_LLM_TIMEOUT", "60")))
        temperature = float(get_config("ANGE_LLM_TEMPERATURE", os.getenv("ANGE_LLM_TEMPERATURE", "0.35")))
    except ValueError:
        timeout, temperature = 60, 0.35

    user_payload = {
        "prompt": req.prompt,
        "media_type": req.media_type,
        "images": req.images or [],
        "image_roles": req.image_roles or [],
        "size": req.size,
        "allow_paid": assistant_allow_paid(),
        "allow_agnes": assistant_allow_agnes(),
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": load_assistant_system_prompt()},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
            },
        )
    if resp.status_code >= 400:
        raise BackendUnavailable(f"Ange assistant upstream request failed (HTTP {resp.status_code})")
    try:
        data = resp.json()
    except ValueError as exc:
        raise BackendUnavailable("Ange assistant upstream returned invalid JSON") from exc
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    try:
        return parse_llm_json_content(content)
    except Exception as exc:
        raise BackendUnavailable("Ange assistant did not return a valid JSON plan") from exc


async def build_assistant_plan(req: AssistantRequest) -> dict[str, Any]:
    llm_plan = await call_llm_for_plan(req)
    llm_used = llm_plan is not None
    if llm_plan is None:
        route_req = RouteRequest(prompt=req.prompt, media_type=req.media_type, images=req.images, requested_model=None, size=req.size)
        route = build_route_response(route_req)
        enhance = EnhanceRequest(prompt=req.prompt, media_type=route["media_type"])
        enhanced_prompt, changed, notes = enhance_prompt_text(enhance)
        llm_plan = {
            **route,
            "prompt": enhanced_prompt,
            "enhanced": changed,
            "assistant_mode": "rule_fallback",
            "notes": notes,
        }
    plan = sanitize_assistant_plan(llm_plan, req)
    plan["assistant_mode"] = "llm" if llm_used else str(plan.get("assistant_mode") or "rule_fallback")
    plan_id = uuid.uuid4().hex
    save_assistant_plan(plan_id, req.prompt, plan["media_type"], plan)
    plan["plan_id"] = plan_id
    return plan
