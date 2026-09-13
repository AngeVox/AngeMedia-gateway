"""Prepare safe skill/tool context before assistant answer synthesis."""
from __future__ import annotations

from dataclasses import dataclass

from ..job_sanitizer import sanitize_error_text
from .assistant_skills import AssistantSkill
from .assistant_tool_planner import plan_assistant_tools, select_chat_skill
from .assistant_tools import AssistantToolError, AssistantToolExecutor, AssistantToolResult


@dataclass(frozen=True)
class AssistantPreparedContext:
    skill: AssistantSkill
    tool_results: tuple[AssistantToolResult, ...]
    kb_hits: tuple[dict[str, str], ...]

    def safe_tool_results(self) -> list[dict]:
        return [item.as_dict() for item in self.tool_results]


def _error_result(tool_name: str, exc: AssistantToolError) -> AssistantToolResult:
    return AssistantToolResult(
        tool=tool_name,
        status="error",
        summary=sanitize_error_text(str(exc), limit=240),
        data={},
    )


def _kb_hits(results: list[AssistantToolResult]) -> tuple[dict[str, str], ...]:
    for result in results:
        if result.tool != "local_knowledge_base" or result.status != "done":
            continue
        raw_hits = result.data.get("hits")
        if not isinstance(raw_hits, list):
            return ()
        return tuple(
            {
                "source": str(item.get("source") or "")[:128],
                "summary": str(item.get("summary") or "")[:900],
            }
            for item in raw_hits
            if isinstance(item, dict)
        )
    return ()


async def prepare_assistant_context_async(
    message: str,
    *,
    executor: AssistantToolExecutor | None = None,
) -> AssistantPreparedContext:
    """Prepare context with both synchronous and asynchronous read-only tools."""
    skill = select_chat_skill(message)
    tool_executor = executor or AssistantToolExecutor()
    calls = plan_assistant_tools(message, skill)
    results: list[AssistantToolResult] = []
    for tool_name, args in calls:
        try:
            results.append(await tool_executor.execute_async(skill, tool_name, args))
        except AssistantToolError as exc:
            results.append(_error_result(tool_name, exc))
    return AssistantPreparedContext(
        skill=skill,
        tool_results=tuple(results),
        kb_hits=_kb_hits(results),
    )
