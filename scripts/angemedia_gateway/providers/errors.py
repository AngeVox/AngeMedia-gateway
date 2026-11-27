"""Structured provider errors and safe API envelopes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ProviderError(RuntimeError):
    """Base class for provider errors.

    The message must be safe for logs and API responses. Never pass raw
    upstream bodies, secrets, tracebacks, or provider request payloads here.
    """

    message: str
    status_code: int | None = None
    retryable: bool = False
    error_category: str = "upstream"
    # 已受理或提交结果不明时，调用方只能取回原任务，不能转投生成。
    request_committed: bool = False

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.message)

    def __str__(self) -> str:
        return self.message


class ProviderRateLimited(ProviderError):
    """429, quota exhaustion, or provider-side rate limit."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = 429,
        retryable: bool = True,
        error_category: str = "rate_limit",
    ) -> None:
        super().__init__(message=message, status_code=status_code, retryable=retryable, error_category=error_category)


class ProviderUnavailable(ProviderError):
    """Upstream unavailable, network failure, or unexpected 5xx."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        error_category: str = "upstream",
    ) -> None:
        super().__init__(message=message, status_code=status_code, retryable=retryable, error_category=error_category)


class BackendUnavailable(ProviderUnavailable):
    """Provider unavailable alias used by current gateway code."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        error_category: str = "upstream",
    ) -> None:
        super().__init__(message=message, status_code=status_code, retryable=retryable, error_category=error_category)


class ProviderAuthError(BackendUnavailable):
    """401 / 403 authentication or authorization failure."""

    def __init__(self, message: str, *, status_code: int | None = 401) -> None:
        super().__init__(message=message, status_code=status_code, retryable=False, error_category="auth")


class ProviderQueueFull(BackendUnavailable):
    """渠道明确拒绝受理，未创建任务。"""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message=message, status_code=status_code, error_category="queue_full")


class ProviderTimeout(BackendUnavailable):
    """Provider connect/read timeout."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message=message, status_code=status_code, retryable=True, error_category="timeout")


class ProviderValidationError(BackendUnavailable):
    """Provider or gateway rejected the request as invalid."""

    def __init__(self, message: str, *, status_code: int | None = 400) -> None:
        super().__init__(message=message, status_code=status_code, retryable=False, error_category="validation")


class ProviderProtocolError(BackendUnavailable):
    """Provider response shape cannot be parsed safely."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message=message, status_code=status_code, retryable=False, error_category="protocol")


class ProviderTaskFailed(BackendUnavailable):
    """服务方明确返回任务失败；可携带已观察状态，不能用本地超时冒充。"""

    def __init__(self, message: str, *, status_code: int | None = None, provider_status: str | None = None) -> None:
        super().__init__(message=message, status_code=status_code, retryable=False, error_category="task_failed")
        self.provider_status = provider_status


class RateLimited(ProviderRateLimited):
    """Provider rate limit alias used by current gateway code."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = 429,
        retryable: bool = True,
        error_category: str = "rate_limit",
    ) -> None:
        super().__init__(message=message, status_code=status_code, retryable=retryable, error_category=error_category)


def provider_error_envelope(
    error: ProviderError,
    *,
    provider: str | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    """Return a safe provider error envelope for API/job diagnostics."""

    envelope: dict[str, Any] = {
        "message": str(error),
        "error_category": error.error_category,
        "retryable": error.retryable,
    }
    if error.status_code is not None:
        envelope["status_code"] = error.status_code
    if provider:
        envelope["provider"] = provider
    if stage:
        envelope["gateway_stage"] = stage
    return envelope
