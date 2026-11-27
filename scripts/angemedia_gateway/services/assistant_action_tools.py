"""原生SDK适配；提案写入与原只读工具栈分离，确认不注册为模型工具。"""
from functools import lru_cache
import json
from typing import Annotated, Any

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolArg, StructuredTool
from pydantic import Field, create_model

from ..assistant_execution import RunExecution
from .assistant_action_specs import ACTION_TOOLS
from .assistant_action_tool_executor import execute_action_tool


def _tool(spec):
    schema = create_model(spec.name + 'RuntimeArgs', __base__=spec.schema,
        runtime=(Annotated[Any, InjectedToolArg], Field(default=None, exclude=True)))

    async def invoke(runtime: ToolRuntime[RunExecution], **arguments):
        if not isinstance(runtime.context, RunExecution):
            raise ValueError('缺少可信执行上下文')
        result = await execute_action_tool(runtime.context, spec.name, arguments, runtime.tool_call_id)
        return ToolMessage(content=json.dumps(result, ensure_ascii=False, separators=(',', ':')),
                           tool_call_id=runtime.tool_call_id, status='error' if result['status'] == 'error' else 'success')
    return StructuredTool.from_function(coroutine=invoke, name=spec.name, description=spec.description,
        args_schema=schema, metadata={'read_only': 'proposal_write' not in spec.effects, 'effects': list(spec.effects)})


@lru_cache(maxsize=1)
def action_tools():
    return tuple(_tool(spec) for spec in ACTION_TOOLS)
