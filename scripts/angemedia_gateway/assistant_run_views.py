"""运行与持久事件的公开投影；不输出输入、请求键或执行租约。"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid5

from .assistant_sanitizer import runtime_payload, sanitize_assistant_text

RUN_FIELDS = (
    "id", "session_id", "status", "created_at", "updated_at", "started_at", "completed_at",
    "event_seq", "timeout_ms", "deadline_ms", "max_model_calls", "model_calls", "max_tool_calls", "tool_calls",
)


def public_run(run: dict[str, Any], answer: str | None) -> dict[str, Any]:
    return {**{key: run[key] for key in RUN_FIELDS},
            "user_message_id": uuid5(UUID(run['id']), 'user').hex,
            "assistant_message_id": uuid5(UUID(run['id']), 'assistant').hex,
            "output": json.loads(runtime_payload(json.loads(run["output_json"]))),
            "answer": sanitize_assistant_text(answer, limit=8000) if answer is not None else None}


def public_event(row) -> dict[str, Any]:
    return {"run_id": row["run_id"], "sequence": row["sequence"], "event_type": row["event_type"],
            "payload": json.loads(runtime_payload(json.loads(row["payload_json"]))), "created_at": row["created_at"]}
