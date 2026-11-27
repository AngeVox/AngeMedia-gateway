"""媒体路由与轻量提示词增强。"""
from __future__ import annotations

from typing import Any, Optional

from . import config as C
from .providers.base import RouteTarget
from .model_routes import catalog_default_image_chain, catalog_image_aliases, resolve_image_target
from .providers.parsers import parse_size
from .schemas import EnhanceRequest, RouteRequest
from .repositories.settings import builtin_provider_enabled
from .video_models import DEFAULT_AGNES_VIDEO_MODEL, agnes_video_profile

MODEL_ALIASES = catalog_image_aliases()
# 保留既有通用别名的环境变量覆盖；具体目录ID始终表示对应型号。
MODEL_ALIASES.update({
    "pollinations": RouteTarget("pollinations", C.POLLINATIONS_DEFAULT_MODEL),
    "openai-image": RouteTarget("openai_image", C.OPENAI_IMAGE_MODEL),
    "agnes-image": RouteTarget("agnes_image", C.AGNES_IMAGE_MODEL),
})
DEFAULT_CHAIN = catalog_default_image_chain()


def route_target_enabled(target: RouteTarget) -> bool:
    return builtin_provider_enabled(target.provider)

VIDEO_TRIGGERS = ("视频", "动起来", "动画", "运动", "文生视频", "图生视频", "animate", "animation", "video", "motion", "i2v")
TEXT_TRIGGERS = ("标题", "文字", "海报", "标语", "slogan", "headline", "poster", "banner")
PORTRAIT_TRIGGERS = ("写真", "真人", "真实", "照片", "现实风格", "写实", "人像", "肖像", "帅哥", "男性", "男生", "美女", "模特", "photoreal", "portrait", "realistic")
PRODUCT_TRIGGERS = ("产品", "商品", "家居", "室内", "自然光", "风景", "landscape", "product", "interior")
CONCEPT_TRIGGERS = ("超现实", "概念", "梦境", "抽象", "surreal", "concept")
ANIME_TRIGGERS = ("二次元", "动漫", "动画风", "插画", "anime", "manga", "illustration")


def resolve_chain(model: Optional[str]) -> list[RouteTarget]:
    if not model:
        return [target for target in DEFAULT_CHAIN if route_target_enabled(target)]

    target = resolve_image_target(model, MODEL_ALIASES)
    return [target] if route_target_enabled(target) else []


def contains_any(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def infer_media_type(prompt: str, media_type: str) -> str:
    if media_type != "auto":
        return media_type
    return "video" if contains_any(prompt, VIDEO_TRIGGERS) else "image"


def infer_video_input_mode(prompt: str, images: Optional[list[str]]) -> str:
    count = len(images or [])
    if count >= 2 and contains_any(prompt, ("过渡", "首尾", "从第一张", "到第二张", "keyframe", "transition")):
        return "first_last_frame"
    if count >= 1 and contains_any(prompt, ("参考", "保持风格", "reference")):
        return "reference"
    if count >= 1:
        return "first_frame"
    return "t2v"


def choose_image_model(prompt: str, requested_model: Optional[str] = None) -> Optional[str]:
    if requested_model:
        return requested_model
    if contains_any(prompt, ("agnes", "Agnes")):
        return "agnes-image"
    if contains_any(prompt, ("gpt-image", "openai-image")):
        return "openai-image"
    if contains_any(prompt, TEXT_TRIGGERS) or contains_any(prompt, ANIME_TRIGGERS):
        return "qwen"
    if contains_any(prompt, PORTRAIT_TRIGGERS):
        return "z-turbo"
    if contains_any(prompt, PRODUCT_TRIGGERS):
        return "flux"
    if contains_any(prompt, CONCEPT_TRIGGERS):
        return "z-image"
    return None


def choose_default_size(prompt: str, media_type: str) -> str:
    if media_type == "video":
        return "720P"
    if contains_any(prompt, ("竖屏", "手机壁纸", "小红书", "portrait", "vertical")):
        return "960x1280"
    if contains_any(prompt, ("横版", "封面", "banner", "landscape", "wide")):
        return "1536x1024"
    return "1024x1024"


def should_enhance_prompt(prompt: str, strength: str) -> bool:
    if strength in {"light", "medium", "strong"}:
        return True
    stripped = prompt.strip()
    if not stripped:
        return False
    detail_groups = (
        ("写实", "真实", "二次元", "动漫", "电影感", "摄影", "插画", "海报", "产品", "风景", "anime", "cinematic"),
        ("构图", "半身", "全身", "特写", "广角", "近景", "远景", "视角", "镜头", "景深"),
        ("光影", "光线", "自然光", "逆光", "柔光", "色彩", "氛围", "背景"),
        ("服装", "表情", "姿态", "动作", "材质", "细节", "皮肤", "发丝"),
        ("不要", "避免", "负面", "水印", "低清晰度", "畸形", "错误文字"),
    )
    detail_score = sum(1 for group in detail_groups if contains_any(stripped, group))
    request_like = contains_any(stripped, ("帮我", "生成", "画", "做一张", "来一张", "make", "create", "draw"))
    if len(stripped) < 36:
        return True
    if len(stripped) < 90 and detail_score < 3:
        return True
    if request_like and len(stripped) < 160 and detail_score < 4:
        return True
    return False


def enhance_prompt_text(req: EnhanceRequest) -> tuple[str, bool, str]:
    media_type = infer_media_type(req.prompt, req.media_type)
    original = req.prompt.strip()
    if not should_enhance_prompt(original, req.strength):
        return original, False, "当前提示词可直接生成；如需更强风格、镜头、光影或负面限制，可以继续扩写。"

    negative = req.negative_prompt or "水印、低清晰度、畸形、错误文字"
    if media_type == "video":
        enhanced = (
            f"{original}。画面要求：主体动作清晰，运动自然连贯，镜头稳定，有轻微电影感推进或跟随；"
            "保留用户原始情绪和氛围，不要只堆技术镜头参数；光影自然，时间推进顺滑，画面无明显跳变。"
        )
        return enhanced, True, "已补充视频镜头、运动、节奏和氛围信息，并保留原始语义。"

    style = req.style or "高质量视觉"
    enhanced = (
        f"{style}，{original}，主体清晰，构图完整，光影层次丰富，色彩协调，细节自然，"
        f"适合高质量图片生成。不要：{negative}。"
    )
    return enhanced, True, "已补充主体、构图、光影、质感和负面限制。"


def build_route_response(req: RouteRequest) -> dict[str, Any]:
    media_type = infer_media_type(req.prompt, req.media_type)
    images = req.images or []

    if media_type == "video":
        input_mode = infer_video_input_mode(req.prompt, images)
        requested_video_model = str(req.requested_model or "").strip()
        if profile := agnes_video_profile(requested_video_model):
            mode = {
                "t2v": "text",
                "first_frame": "reference",
                "first_last_frame": "keyframe",
                "reference": "reference",
            }.get(input_mode, "text")
            requested_size = str(req.size or "").strip().upper()
            size = requested_size if requested_size in profile.sizes else "720P"
            return {
                "media_type": "video",
                "model": profile.model,
                "input_mode": input_mode,
                "mode": mode,
                "seconds": "5",
                "size": size,
                "aspect_ratio": "16:9",
                "prompt_enhancement_recommended": should_enhance_prompt(req.prompt, "auto"),
                "notes": "Agnes Video 2.5 需要账号具备对应模型权限；参考媒体使用公开 http(s) URL，具体类型和数量以型号合同为准。",
            }

        width, height = 1152, 768
        requested_size = str(req.size or "").strip().lower()
        if requested_size:
            try:
                width, height = parse_size(requested_size)
            except Exception:
                width, height = 1152, 768
        return {
            "media_type": "video",
            "model": DEFAULT_AGNES_VIDEO_MODEL,
            "input_mode": input_mode,
            "mode": "keyframes" if input_mode == "first_last_frame" else None,
            "size": f"{width}x{height}",
            "width": width,
            "height": height,
            "num_frames": 121,
            "frame_rate": 24,
            "prompt_enhancement_recommended": should_enhance_prompt(req.prompt, "auto"),
            "notes": "当前兼容默认值为 Agnes Video v2.0，可用性尚未确认；请结合型号政策与账户权限显式选择。",
        }

    size = req.size or choose_default_size(req.prompt, media_type)
    model = choose_image_model(req.prompt, req.requested_model)
    if model and not model.startswith("custom:"):
        resolve_image_target(model, MODEL_ALIASES)
    return {
        "media_type": "image",
        "model": model,
        "uses_default_chain": model is None,
        "size": size,
        "response_format": "url",
        "prompt_enhancement_recommended": should_enhance_prompt(req.prompt, "auto"),
        "notes": "model 为空表示使用默认链：kolors → qwen → flux → z-image → z-turbo。Pollinations 仅在显式启用并指定模型时作为 experimental provider 使用。",
    }
