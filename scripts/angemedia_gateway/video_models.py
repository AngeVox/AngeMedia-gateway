"""Agnes video model identifiers and lightweight contract helpers."""
from __future__ import annotations

AGNES_VIDEO_V20_MODEL = "agnes-video-v2.0"
AGNES_VIDEO_V25_MODEL = "agnes-video-2.5"
AGNES_VIDEO_V25_ALIASES = frozenset({AGNES_VIDEO_V25_MODEL, "agnes-video-v2.5"})


def is_agnes_video_v25(model: str | None) -> bool:
    return str(model or "").strip().lower() in AGNES_VIDEO_V25_ALIASES
