"""Deterministic prompt enhancement for local, authenticated helper calls."""
from __future__ import annotations

import re
from typing import Any

from ..routing import infer_media_type
from ..schemas import EnhanceRequest
from ..security import redact_secret_text

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
DATA_URL_RE = re.compile(r"data:[A-Za-z0-9.+/-]+;base64,[A-Za-z0-9+/=_-]+", re.I)
LOCAL_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s,;，。]+")
REQUEST_HASH_RE = re.compile(r"\brequest_hash\b\s*[:=]\s*[A-Za-z0-9_.:-]+", re.I)
AUTH_RE = re.compile(r"\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9_.-]+", re.I)
PROVIDER_RAW_RE = re.compile(r"\bprovider[_ -]?raw[_ -]?body\b", re.I)

DETAIL_HINTS = (
    "窗台", "热茶", "下午", "阳光", "房间", "温暖", "安静", "构图", "光影", "色彩",
    "背景", "材质", "细节", "镜头", "景深", "close-up", "composition", "lighting",
    "background", "warm afternoon", "by the window",
)
REQUEST_WORDS = ("帮我", "生成", "画", "做一张", "create", "make", "draw")

ZH_TO_EN_PHRASES: tuple[tuple[str, str], ...] = (
    ("赛博朋克城市夜景", "a cyberpunk city at night"),
    ("赛博朋克", "cyberpunk"),
    ("城市夜景", "city nightscape"),
    ("夜景", "night scene"),
    ("雨天", "rainy weather"),
    ("雨后", "after rain"),
    ("霓虹招牌", "neon signs"),
    ("霓虹", "neon lights"),
    ("电影感", "cinematic mood"),
    ("街头", "street scene"),
    ("产品照片", "product photo"),
    ("陶瓷杯", "ceramic mug"),
    ("一只橘猫", "an orange cat"),
    ("橘猫", "orange cat"),
    ("一只猫", "a cat"),
    ("可爱的猫", "a cute cat"),
    ("猫", "cat"),
    ("小猫咪", "kitten"),
    ("小猫", "kitten"),
    ("毛茸茸", "fluffy"),
    ("奶黄色", "cream-yellow"),
    ("米白色羊毛毯", "off-white wool blanket"),
    ("羊毛毯", "wool blanket"),
    ("蜷缩", "curled up"),
    ("琥珀色", "amber"),
    ("圆眼睛", "round eyes"),
    ("从沙发跳到窗台", "jumping from the sofa to the windowsill"),
    ("坐在窗台上", "sitting on the windowsill"),
    ("窗台", "windowsill"),
    ("窗边", "by the window"),
    ("窗口", "window"),
    ("窗", "window"),
    ("沙发", "sofa"),
    ("热茶", "a cup of hot tea"),
    ("下午阳光", "warm afternoon sunlight"),
    ("午后阳光", "warm afternoon sunlight"),
    ("阳光", "sunlight"),
    ("柔软", "soft"),
    ("木质地板", "wooden floor"),
    ("绿植", "green plants"),
    ("低角度", "low-angle camera"),
    ("浅景深", "shallow depth of field"),
    ("房间", "room"),
    ("温暖安静", "warm and quiet mood"),
    ("温暖", "warm mood"),
    ("安静", "quiet mood"),
)


def _sanitize_text(value: str) -> str:
    text = AUTH_RE.sub("[redacted auth]", str(value or ""))
    text = redact_secret_text(text)
    text = REQUEST_HASH_RE.sub("[redacted hash]", text)
    text = PROVIDER_RAW_RE.sub("[redacted provider body]", text)
    text = DATA_URL_RE.sub("[redacted data url]", text)
    text = LOCAL_PATH_RE.sub("[redacted local path]", text)
    return " ".join(text.split())


def _source_language(prompt: str) -> str:
    return "zh" if CJK_RE.search(prompt) else "en"


def _display_language(req: EnhanceRequest, source_language: str) -> str:
    if req.language in {"zh", "en"}:
        return req.language
    return "zh" if source_language == "zh" else "en"


def _is_detailed(prompt: str, source_language: str, strength: str) -> bool:
    if strength == "light":
        return True
    if source_language == "en" and (len(prompt) >= 28 or "," in prompt):
        return True
    detail_score = sum(1 for word in DETAIL_HINTS if word.lower() in prompt.lower())
    punctuation_score = sum(prompt.count(mark) for mark in ("，", "。", ",", ";", "；"))
    if detail_score >= 3:
        return True
    if len(prompt) >= 42 and detail_score >= 2:
        return True
    if punctuation_score >= 2 and detail_score >= 2:
        return True
    return False


def _english_base(prompt: str, source_language: str) -> str:
    if source_language == "en":
        return prompt.strip().rstrip(".")
    matches: list[str] = []
    for zh, en in ZH_TO_EN_PHRASES:
        if zh in prompt and en not in matches:
            matches.append(en)
    if not matches:
        return "the subject described by the user"
    compact: list[str] = []
    for phrase in matches:
        lower = phrase.lower()
        if any(lower == existing.lower() or lower in existing.lower() for existing in compact):
            continue
        compact = [existing for existing in compact if existing.lower() not in lower]
        compact.append(phrase)
    return ", ".join(compact)


def _english_prompt(base: str, *, media_type: str, mode: str, style: str | None) -> str:
    clean_base = base.strip().rstrip(".,")
    if media_type == "video":
        if mode == "expand":
            return (
                f"{clean_base}, smooth natural motion, a clear action arc, stable camera, "
                "continuous scene, soft natural light, concise realistic shot"
            )
        return (
            f"{clean_base}, preserve the original action and setting, smooth motion, stable camera, "
            "natural continuity, gentle room light"
        )
    style_prefix = f"{style.strip()}, " if style and style.strip() else ""
    if mode == "expand":
        return (
            f"{style_prefix}{clean_base}, clear subject, balanced composition, natural light, "
            "clean background, coherent details, high quality image prompt"
        )
    return (
        f"{style_prefix}{clean_base}, clear subject, balanced composition, natural lighting, "
        "coherent details, preserve the original mood"
    )


def _zh_display(original: str, *, media_type: str, mode: str) -> str:
    if media_type == "video":
        if mode == "expand":
            return f"{original}，动作连贯自然，镜头稳定，场景连续，光线自然。"
        return f"{original}。已保留原始动作和场景，只整理为更适合视频模型理解的描述。"
    if mode == "expand":
        return f"{original}，主体清晰，构图完整，光影自然，背景干净，细节协调。"
    return f"{original}。已保留原始主体、场景和氛围，只整理语序并增强模型可读性。"


def _en_display(model_prompt_en: str, *, mode: str) -> str:
    prefix = "Expanded prompt" if mode == "expand" else "Polished prompt"
    return f"{prefix}: {model_prompt_en}"


def _notes_zh(media_type: str, mode: str) -> list[str]:
    if mode == "expand" and media_type == "video":
        return ["已补充轻量动作、镜头稳定性和场景连续性。", "英文提示词可直接发送给视频模型。"]
    if mode == "expand":
        return ["已补充主体、构图、光影和画面质量描述。", "英文提示词可直接发送给图片模型。"]
    if media_type == "video":
        return ["已保留原始动作和场景，仅整理为更清晰的视频模型描述。", "未添加复杂长剧情或无关风格。"]
    return ["已保留原始主体、场景和氛围，仅做语序整理和模型友好化。", "未添加无关主体、时代或风格。"]


def _notes_en(media_type: str, mode: str) -> list[str]:
    if mode == "expand" and media_type == "video":
        return ["Added light motion, stable-camera, and scene-continuity details.", "The English prompt is ready for video models."]
    if mode == "expand":
        return ["Added subject clarity, composition, lighting, and image-quality details.", "The English prompt is ready for image models."]
    if media_type == "video":
        return ["Preserved the original action and scene while making it clearer for video models.", "No unrelated long story or style was added."]
    return ["Preserved the original subject, scene, and mood while polishing model readability.", "No unrelated subject, era, or style was added."]


def _negative_prompt(req: EnhanceRequest, mode: str) -> str | None:
    if req.negative_prompt:
        return _sanitize_text(req.negative_prompt)[:300]
    if mode == "expand":
        return "watermark, low resolution, distorted anatomy, malformed text"
    return None


def enhance_prompt(req: EnhanceRequest) -> dict[str, Any]:
    original = _sanitize_text(req.prompt)
    source_language = _source_language(original)
    display_language = _display_language(req, source_language)
    media_type = infer_media_type(original, req.media_type)
    mode = "polish" if _is_detailed(original, source_language, req.strength) else "expand"
    base = _english_base(original, source_language)
    model_prompt_en = _english_prompt(base, media_type=media_type, mode=mode, style=req.style)
    user_display_prompt_zh = _zh_display(original, media_type=media_type, mode=mode)
    notes_zh = _notes_zh(media_type, mode)
    user_display_prompt = user_display_prompt_zh if display_language == "zh" else _en_display(model_prompt_en, mode=mode)
    notes = notes_zh if display_language == "zh" else _notes_en(media_type, mode)
    return {
        "mode": mode,
        "changed": True,
        "user_display_prompt": user_display_prompt,
        "user_display_prompt_zh": user_display_prompt_zh,
        "model_prompt_en": model_prompt_en,
        "negative_prompt": _negative_prompt(req, mode),
        "notes": notes,
        "notes_zh": notes_zh,
        "warnings": [],
        "input_summary": {
            "media_type": media_type,
            "source_language": source_language,
            "display_language": display_language,
            "target_language": req.target_language,
            "strength": req.strength,
        },
    }
