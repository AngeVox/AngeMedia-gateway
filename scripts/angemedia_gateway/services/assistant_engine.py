"""成熟Agent内核适配；配置、HTTP、消息投影与运行控制各自维护边界。"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx
import openai
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from langsmith import tracing_context
from pydantic import BaseModel

from ..assistant_async import settle_on_cancel
from ..assistant_checkpoints import open_run_checkpoints
from ..assistant_engine_payloads import EngineFailure, EnginePayloadPolicy
from ..assistant_execution import RunResult
from ..assistant_runtime_contract import RunBudgetExceeded, RunLeaseLost
from ..outbound_http import outbound_client
from .assistant_config_service import resolve_assistant_runtime
from .assistant_conversation import CONVERSATION_POLICY
from .assistant_business_policy import AssistantPolicyDenied


class RuntimeMiddleware(AgentMiddleware):
    def __init__(self, context, policy, tools, *, tool_timeout, model_timeout, authorizer=None):
        self.context, self.policy = context, policy
        self._tools_by_name = tools
        self.tool_timeout = tool_timeout
        self.model_timeout = model_timeout
        self.authorizer = authorizer

    async def _authorize(self):
        if self.authorizer is not None:
            await self.authorizer(self.context)

    async def awrap_model_call(self, request, handler):
        await self._authorize()
        await self.context.reserve_call("model")
        total = sum(len(str(message.content)) for message in request.messages)
        if total > 65536:
            raise EngineFailure("context_budget")
        await self.context.append_event("model_started")
        try:
            response = await asyncio.wait_for(handler(request), self.model_timeout)
        except (openai.APITimeoutError, asyncio.TimeoutError):
            raise EngineFailure("upstream_timeout") from None
        except openai.APIStatusError as exc:
            code = "upstream_auth" if exc.status_code in {401, 403} else "upstream_rate_limit" if exc.status_code == 429 else "upstream_http_error"
            raise EngineFailure(code) from None
        except (RunBudgetExceeded, RunLeaseLost, asyncio.CancelledError):
            raise
        except Exception:
            raise EngineFailure("upstream_error") from None
        await self._authorize()
        result = ModelResponse(result=[self.policy.model_message(message) for message in response.result])
        await self.context.append_event("model_finished")
        return result

    async def awrap_tool_call(self, request, handler):
        await self._authorize()
        await self.context.reserve_call("tool")
        call = request.tool_call
        tool = self._tools_by_name.get(call["name"])
        if tool is None:
            return ToolMessage(content="unknown_tool", tool_call_id=call["id"], status="error")
        try:
            # 公开schema排除SDK注入字段；即使值为空，也不接受模型伪造runtime。
            tool.tool_call_schema.model_validate(call["args"], strict=True, extra="forbid")
        except Exception:
            return ToolMessage(content="invalid_tool_arguments", tool_call_id=call["id"], status="error")
        await self.context.append_event("tool_started", {"name": tool.name, "call_id": call["id"]})
        try:
            result = await asyncio.wait_for(handler(request), self.tool_timeout)
            result = self.policy.tool_message(result)
        except (RunBudgetExceeded, RunLeaseLost, AssistantPolicyDenied, asyncio.CancelledError):
            raise
        except asyncio.TimeoutError:
            result = ToolMessage(content="tool_timeout", tool_call_id=call["id"], status="error")
        except EngineFailure:
            raise
        except Exception:
            result = ToolMessage(content="tool_failed", tool_call_id=call["id"], status="error")
        await self._authorize()
        await self.context.append_event("tool_finished", {"name": tool.name, "call_id": call["id"], "status": result.status})
        return result


class AssistantEngine:
    def __init__(self, checkpoint_db: Path, tools=(), *, config_resolver=resolve_assistant_runtime,
                 model_timeout=30.0, tool_timeout=15.0, authorizer=None, context_schema=None, payload_secrets=()):
        self.checkpoint_db = Path(checkpoint_db)
        self.config_resolver = config_resolver
        self.authorizer, self.context_schema = authorizer, context_schema
        self.payload_secrets = tuple(payload_secrets)
        if not 0 < model_timeout <= 120 or not 0 < tool_timeout <= 120:
            raise ValueError("模型和工具时限必须大于0且不超过120秒")
        self.model_timeout, self.tool_timeout = model_timeout, tool_timeout
        self.tools = {}
        for tool in tools:
            if (not isinstance(tool, StructuredTool) or tool.coroutine is None or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", tool.name)
                or not isinstance(tool.args_schema, type) or not issubclass(tool.args_schema, BaseModel)
                or tool.args_schema.model_config.get("extra") != "forbid"
                or tool.args_schema.model_config.get("strict") is not True or tool.name in self.tools):
                raise ValueError("工具必须是唯一命名、异步执行、严格参数且禁止额外字段的StructuredTool")
            self.tools[tool.name] = tool
        if len(self.tools) > 32:
            raise ValueError("单次工具注册最多32个")

    async def __call__(self, context):
        runtime = await settle_on_cancel(asyncio.to_thread(self.config_resolver))
        if not runtime.base_url or not runtime.model:
            return RunResult("failed", {"error_code": "assistant_not_configured"})
        policy = EnginePayloadPolicy(secrets=(runtime.api_key, context.lease.owner_token, *self.payload_secrets))
        try:
            # 追踪只在本次异步上下文关闭，不改写全局环境或其他会话设置。
            with tracing_context(enabled=False, parent=False):
                return await self._invoke(context, runtime, policy)
        except EngineFailure as exc:
            code = str(exc)
            await context.append_event("engine_failed", {"code": code})
            return RunResult("refused" if code == "upstream_refusal" else "failed", {"error_code": code})
        except GraphRecursionError:
            return RunResult("failed", {"error_code": "engine_step_budget"})
        except AssistantPolicyDenied as exc:
            await context.append_event("policy_denied", {"code": str(exc)})
            return RunResult("refused", {"error_code": str(exc)})

    async def _invoke(self, context, runtime, policy):
        async with outbound_client(timeout=self.model_timeout) as client:
            # 只走异步入口；同步SDK客户端显式禁用环境代理、重定向及自动重试。
            with httpx.Client(trust_env=False, follow_redirects=False) as sync_client:
                model = ChatOpenAI(model=runtime.model, api_key=runtime.api_key or "unused", base_url=runtime.base_url,
                    temperature=0.1, max_tokens=2048, max_retries=0, timeout=self.model_timeout, cache=False,
                    use_responses_api=False, http_client=sync_client, http_async_client=client)
                async with open_run_checkpoints(context.repository.db_path, self.checkpoint_db, context.lease,
                    clock=context.repository.clock, payload_guard=policy.checkpoint_guard) as saver:
                    agent = create_agent(model, list(self.tools.values()), system_prompt=CONVERSATION_POLICY,
                        context_schema=self.context_schema,
                        middleware=[RuntimeMiddleware(context, policy, self.tools, tool_timeout=self.tool_timeout,
                            model_timeout=self.model_timeout, authorizer=self.authorizer)], checkpointer=saver)
                    config = {"configurable": {"thread_id": context.lease.run_id, "checkpoint_ns": ""},
                              "recursion_limit": 64, "callbacks": []}
                    existing = await saver.aget_tuple(config)
                    value = None
                    if existing is None:
                        valid_input = isinstance(context.input, dict) and set(context.input) in ({'message'}, {'message', 'language'})
                        language = context.input.get('language') if valid_input else None
                        text = context.input.get('message') if valid_input and language in (None, 'zh', 'en') else None
                        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
                            raise EngineFailure("invalid_input")
                        text = policy.text(text)
                        await context.publish_message("user", text)
                        rows = await settle_on_cancel(asyncio.to_thread(context.repository.context_history, context.lease))
                        value = {"messages": policy.initial_messages(rows, text, language=language)}
                    result = await agent.ainvoke(value, config, durability="sync",
                                                **({"context": context} if self.context_schema is not None else {}))
                    last = result["messages"][-1]
                    if not isinstance(last, AIMessage) or last.tool_calls or not last.content:
                        raise EngineFailure("incomplete_engine_result")
                    return RunResult(answer=policy.text(last.content), output={"engine": "native_tools"})
