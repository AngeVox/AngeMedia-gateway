"""模型消息和工具结果的有界投影；正文脱敏不承担主题过滤。"""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from .assistant_sanitizer import runtime_payload, sanitize_assistant_text
from .job_sanitizer import sanitize_job_value
from .assistant_action_contract import PROPOSAL_TOOL_ERRORS
from .services.assistant_conversation import conversation_history


class EngineFailure(RuntimeError):
    """只传递固定诊断分类，不携带原始供应商正文。"""

    def __init__(self, code):
        allowed = {"invalid_tool_arguments", "unsafe_tool_arguments", "invalid_tool_protocol", "upstream_refusal",
                   "output_budget", "empty_content", "unsupported_tool_result", "invalid_tool_result",
                   "invalid_checkpoint_payload", "checkpoint_budget", "unsafe_checkpoint_payload", "context_budget", "tool_result_budget",
                   "upstream_timeout", "upstream_auth", "upstream_rate_limit", "upstream_http_error", "upstream_error",
                   "invalid_input", "incomplete_engine_result"}
        super().__init__(code if code in allowed else "invalid_tool_result")


class EnginePayloadPolicy:
    def __init__(self, *, secrets=()):
        self._secrets = tuple(value for value in secrets if value)

    def text(self, value, *, limit=8000):
        text = str(value or "")
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        return sanitize_assistant_text(text, limit=limit)

    def arguments(self, arguments):
        try:
            runtime_payload(arguments)
            safe = sanitize_job_value(arguments)
        except ValueError:
            raise EngineFailure("invalid_tool_arguments") from None
        # 参数与正文不同，脱敏改变动作参数时停止该调用，不能悄悄换成另一个动作。
        if safe != arguments or any(secret in json.dumps(safe, ensure_ascii=False) for secret in self._secrets):
            raise EngineFailure("unsafe_tool_arguments")
        return safe

    def model_message(self, message):
        if not isinstance(message, AIMessage) or message.invalid_tool_calls:
            raise EngineFailure("invalid_tool_protocol")
        reason = message.response_metadata.get("finish_reason")
        if message.additional_kwargs.get("refusal") or reason == "content_filter":
            raise EngineFailure("upstream_refusal")
        if reason == "length":
            raise EngineFailure("output_budget")
        content = message.content
        if isinstance(content, list):
            content = "\n".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
        calls = [{"id": call["id"], "name": call["name"], "args": self.arguments(call["args"]), "type": "tool_call"}
                 for call in message.tool_calls]
        if len(calls) > 16 or len({call["id"] for call in calls}) != len(calls) or any(not isinstance(call["id"], str) or not 1 <= len(call["id"]) <= 128 for call in calls):
            raise EngineFailure("invalid_tool_protocol")
        content = self.text(content)
        if not content and not calls:
            raise EngineFailure("empty_content")
        # 不持久化reasoning、响应头、原始tool JSON、usage或供应商扩展字段。
        return AIMessage(content=content, tool_calls=calls, id=message.id)

    def tool_message(self, message):
        if not isinstance(message, ToolMessage):
            raise EngineFailure("unsupported_tool_result")
        content = message.content
        if message.status == "error":
            # 只保留服务端固定错误信封，不能把SDK校验异常或原始上游正文回传模型。
            try:
                error = json.loads(content)
            except (TypeError, ValueError):
                error = None
            allowed = {"not_found", "invalid_tool_arguments", "business_read_failed", "unknown_tool"} | PROPOSAL_TOOL_ERRORS
            content = runtime_payload(error) if (isinstance(error, dict) and set(error) == {"tool", "status", "code"}
                and error.get("status") == "error" and error.get("code") in allowed
                and isinstance(error.get("tool"), str) and error["tool"].replace("_", "").isalnum()
                and len(error["tool"]) <= 64) else "invalid_tool_arguments"
        if not isinstance(content, str):
            try:
                content = runtime_payload(content)
            except ValueError:
                raise EngineFailure("invalid_tool_result") from None
        # JSON结果按键脱敏；普通创作文本不要求是JSON。
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            parsed = None
        else:
            try:
                content = runtime_payload(parsed)
            except ValueError:
                raise EngineFailure("invalid_tool_result") from None
        if len(content) > 8000:
            raise EngineFailure("tool_result_budget")
        return ToolMessage(content=self.text(content), tool_call_id=message.tool_call_id,
                           name=message.name, status=message.status, id=message.id)

    def initial_messages(self, rows, text, *, language=None):
        history = conversation_history(rows, sanitize=self.text)
        current = self.text(text)
        if language is not None:
            current = json.dumps({'question': current, 'language': language}, ensure_ascii=False)
        return [(HumanMessage if row["role"] == "user" else AIMessage)(content=row["content"]) for row in history] + [HumanMessage(content=current)]

    def checkpoint_guard(self, value, serde):
        try:
            _type, encoded = serde.dumps_typed(value)
        except Exception:
            raise EngineFailure("invalid_checkpoint_payload") from None
        if len(encoded) > 262144:
            raise EngineFailure("checkpoint_budget")
        if any(secret.encode() in encoded for secret in self._secrets):
            raise EngineFailure("unsafe_checkpoint_payload")
