"""SDK工具适配：严格公开参数与隐藏ToolRuntime分开，不把权限放进模型输入。"""
from __future__ import annotations

from functools import lru_cache
import json
from typing import Annotated, Any

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolArg, StructuredTool
from pydantic import Field, create_model

from ..assistant_execution import RunExecution
from .assistant_business_executor import execute_business_tool
from .assistant_business_specs import BUSINESS_TOOLS


def _tool(spec):
    schema = create_model(spec.name + "RuntimeArgs", __base__=spec.schema,
        runtime=(Annotated[Any, InjectedToolArg], Field(default=None, exclude=True)))

    async def invoke(runtime: ToolRuntime[RunExecution], **arguments):
        if not isinstance(runtime.context, RunExecution):
            raise ValueError("缺少可信执行上下文")
        result = await execute_business_tool(runtime.context, spec.name, arguments)
        return ToolMessage(content=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                           tool_call_id=runtime.tool_call_id, status="error" if result["status"] == "error" else "success")

    return StructuredTool.from_function(coroutine=invoke, name=spec.name, description=spec.description,
        args_schema=schema, metadata={"read_only": True, "effects": list(spec.effects)})


@lru_cache(maxsize=1)
def business_tools():
    return tuple(_tool(spec) for spec in BUSINESS_TOOLS)
