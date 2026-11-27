"""只读业务执行边界；权限与执行分开，完整线程收尾后再返回或传递取消。"""
from __future__ import annotations

import asyncio
import inspect

from ..assistant_async import settle_on_cancel
from ..assistant_runtime_contract import RunLeaseLost
from ..db.readonly_scope import readonly_database
from .assistant_business_policy import AssistantPolicyDenied, authorize_business, authorize_business_sync
from .assistant_business_reads import bounded_result, read_assets, read_diagnostics, read_queue, read_jobs
from .assistant_business_specs import BUSINESS_TOOLS
from .assistant_business_secrets import configured_secrets, redact_values
from .assistant_tools import AssistantToolNotFound, AssistantToolRegistry, project_assistant_tool_result

_SPECS = {spec.name: spec for spec in BUSINESS_TOOLS}
_OVERRIDES = {"job_search": read_jobs, "asset_search": read_assets, "diagnostics_summary": read_diagnostics, "queue_status": read_queue}


def _read(context, name, args):
    # 线程排队期间权限可能已撤销，真正开始读取/探测之前再次核对。
    authorize_business_sync(context)
    with readonly_database(context.repository.db_path):
        secrets = configured_secrets()
        definition = AssistantToolRegistry().get(name)
        handler = _OVERRIDES.get(name) or (definition.handler if definition else None)
        if handler is None:
            raise ValueError("未注册的业务工具")
        payload = handler(args)
        if inspect.isawaitable(payload):
            # 旧探针的同步配置读取也必须离开主循环；网络调用使用其原有有界时限。
            payload = asyncio.run(payload)
        result = project_assistant_tool_result(name, bounded_result(name, args, payload))
        secrets = tuple(set(secrets) | set(configured_secrets()))
        return {**result, "summary": redact_values(result["summary"], secrets), "data": redact_values(result["data"], secrets)}


async def execute_business_tool(context, name: str, arguments):
    await authorize_business(context)
    spec = _SPECS.get(name)
    if spec is None:
        return {"tool": name, "status": "error", "code": "unknown_tool"}
    try:
        args = spec.schema.model_validate(arguments, strict=True).model_dump()
    except ValueError:
        return {"tool": name, "status": "error", "code": "invalid_tool_arguments"}
    try:
        result = await settle_on_cancel(asyncio.to_thread(_read, context, name, args))
    except AssistantToolNotFound:
        result = {"tool": name, "status": "error", "code": "not_found"}
    except (RunLeaseLost, AssistantPolicyDenied):
        raise
    except Exception:
        result = {"tool": name, "status": "error", "code": "business_read_failed"}
    await authorize_business(context)
    return result
