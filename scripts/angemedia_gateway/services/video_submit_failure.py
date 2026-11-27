"""视频提交失败的语义判断，与任务持久化及调度分离。"""
from __future__ import annotations

from dataclasses import dataclass

from ..job_sanitizer import sanitize_error_text
from ..providers.errors import ProviderAuthError, ProviderError, ProviderQueueFull, ProviderTimeout, ProviderValidationError


@dataclass(frozen=True)
class VideoSubmitFailure:
    """只包含可公开诊断；提交失败不在这里授权自动重提。"""

    error_code: str
    error_category: str
    human_hint: str


def classify_video_submit_failure(error: Exception) -> VideoSubmitFailure:
    """区分明确拒绝与未确认的提交。"""
    if isinstance(error, ProviderQueueFull):
        return VideoSubmitFailure(
            "video_submit_queue_full", error.error_category, "渠道队列已满，本次未受理。请稍后再试。",
        )
    status = error.status_code if isinstance(error, ProviderError) else None
    if isinstance(error, ProviderAuthError) or status in {401, 403}:
        return VideoSubmitFailure(
            "video_submit_rejected", "auth_failed",
            "视频服务商拒绝了认证或模型访问权限；请检查渠道 API Key、模型权限和账户状态后再提交。",
        )
    if isinstance(error, ProviderValidationError):
        return VideoSubmitFailure(
            "video_submit_rejected", "invalid_request",
            "视频服务商拒绝了请求参数；请核对所选模型、时长、清晰度和参考素材后再提交。",
        )

    # 保留旧适配器的安全错误文本判断；未知响应仍按不确定提交处理。
    safe_error = sanitize_error_text(str(error)).lower()
    if isinstance(error, ProviderTimeout) or "timeout" in safe_error:
        hint = (
            "视频提交超时，渠道未返回任务号。系统不会自动重提。请先到渠道平台核对任务和扣费记录。"
        )
    else:
        hint = "渠道没有返回任务号。请先核对渠道上的任务和扣费记录，系统不会自动重提。"
    return VideoSubmitFailure("video_submit_ambiguous", "ambiguous_submit", hint)
