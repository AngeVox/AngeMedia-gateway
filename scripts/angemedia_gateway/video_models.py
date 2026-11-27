"""视频型号标识及请求默认值的唯一来源。"""
from __future__ import annotations

from dataclasses import dataclass

AGNES_VIDEO_V20_MODEL = "agnes-video-v2.0"
DEFAULT_AGNES_VIDEO_MODEL = AGNES_VIDEO_V20_MODEL
AGNES_VIDEO_V25_MODEL = "agnes-video-2.5"
AGNES_VIDEO_FLASH_MODEL = "agnes-video-2.5-flash"
AGNES_VIDEO_V25_ALIASES = frozenset({AGNES_VIDEO_V25_MODEL, "agnes-video-v2.5"})


def is_agnes_video_v25(model: str | None) -> bool:
    return str(model or "").strip().lower() in AGNES_VIDEO_V25_ALIASES


@dataclass(frozen=True, slots=True)
class AgnesVideoProfile:
    """同一视频协议下的型号差异；校验、适配和目录投影共用。"""

    model: str
    sizes: tuple[str, ...]
    max_images: int
    max_audios: int = 3
    max_videos: int = 0
    max_total: int = 8

    def api_contract(self) -> dict:
        return {
            "model": self.model, "sizes": list(self.sizes),
            "max_images": self.max_images, "max_audios": self.max_audios,
            "max_videos": self.max_videos, "max_total": self.max_total,
        }


_V25_PROFILE = AgnesVideoProfile(AGNES_VIDEO_V25_MODEL, ("720P", "1080P", "1K", "2K"), 8, max_videos=1, max_total=12)
_FLASH_PROFILE = AgnesVideoProfile(AGNES_VIDEO_FLASH_MODEL, ("720P",), 5)


def agnes_video_profile(model: str | None) -> AgnesVideoProfile | None:
    normalized = str(model or "").strip().lower()
    if normalized in AGNES_VIDEO_V25_ALIASES:
        return _V25_PROFILE
    return _FLASH_PROFILE if normalized == AGNES_VIDEO_FLASH_MODEL else None


def is_agnes_video_modern(model: str | None) -> bool:
    return agnes_video_profile(model) is not None
