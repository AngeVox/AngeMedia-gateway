"""Pollinations 首个视频型号的公开请求合同。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

POLLINATIONS_VIDEO_MODEL = "alibaba/wan-2.2-fast"
POLLINATIONS_VIDEO_ALIASES = frozenset({"wan2.2", "wan-2.2", "wan-fast", "pollinations-wan-2-2-fast"})
POLLINATIONS_VIDEO_MAX_ATTEMPTS = 96


class PollinationsVideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["pollinations"]
    model: Literal["alibaba/wan-2.2-fast"] = POLLINATIONS_VIDEO_MODEL
    prompt: str = Field(min_length=1, max_length=2000)
    seconds: Literal["5"] = "5"
    size: Literal["480p"] = "480p"
    mode: Literal["text", "keyframe"] | None = None
    first_frame: str | None = Field(default=None, min_length=1, max_length=2048)
    last_frame: str | None = Field(default=None, min_length=1, max_length=2048)
    wait_for_completion: Literal[False] = False

    @model_validator(mode="after")
    def validate_frames(self):
        mode = "keyframe" if self.first_frame else "text"
        if (self.last_frame and not self.first_frame) or (self.mode and self.mode != mode):
            raise ValueError("Pollinations video keyframes require a first frame")
        self.mode = mode
        return self


def is_pollinations_video_job(job):
    return job.get("kind") == "video" and job.get("provider") == "pollinations"
