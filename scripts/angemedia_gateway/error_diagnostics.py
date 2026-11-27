"""安全渠道诊断；区分本地保护预算与上游认证、余额及限流。"""
from __future__ import annotations


def classify_provider_error(message: str) -> dict:
    """
    Classify provider error into structured fields.

    Returns dict with:
    - error_category
    - human_hint
    - retryable
    - gateway_stage
    """
    msg_lower = message.lower()

    # 聚合执行错误仍保留机器标记，不能误提示为平台余额不足或可立即重试。
    if "local_budget_exhausted" in msg_lower:
        return {
            "error_category": "local_budget",
            "human_hint": "已达到本地每日提交尝试预算，请调整网关预算或等待次日；这不表示平台余额已耗尽",
            "retryable": False,
            "gateway_stage": "provider_request",
        }

    # model_unavailable
    if any(term in msg_lower for term in ["model disabled", "model not found", "unavailable", "30003"]):
        return {
            "error_category": "model_unavailable",
            "human_hint": "请更换模型或检查服务商模型是否可用",
            "retryable": False,
            "gateway_stage": "provider_response",
        }

    # content_filtered
    if any(term in msg_lower for term in ["content policy", "filtered", "safety", "moderation"]):
        return {
            "error_category": "content_filtered",
            "human_hint": "请调整提示词内容后重试",
            "retryable": False,
            "gateway_stage": "provider_response",
        }

    # auth_failed
    if any(term in msg_lower for term in ["401", "403", "unauthorized", "invalid api key", "authentication"]):
        return {
            "error_category": "auth_failed",
            "human_hint": "请检查 Provider API Key 或认证配置",
            "retryable": False,
            "gateway_stage": "provider_response",
        }

    # quota_exceeded or provider_rate_limited
    if any(term in msg_lower for term in ["429", "rate limit", "quota", "insufficient balance"]):
        return {
            "error_category": "provider_rate_limited",
            "human_hint": "请稍后重试或检查服务商额度/限速",
            "retryable": True,
            "gateway_stage": "provider_response",
        }

    # unknown_provider_error
    return {
        "error_category": "unknown_provider_error",
        "human_hint": "请稍后重试；如果持续失败，请检查 Provider 配置",
        "retryable": True,
        "gateway_stage": "provider_response",
    }


def classify_duplicate_error() -> dict:
    """Classify duplicate in-flight error."""
    return {
        "error_category": "duplicate_in_flight",
        "human_hint": "已有相同请求正在处理中，请稍后查看任务结果",
        "retryable": True,
        "gateway_stage": "dedupe_admission",
    }
