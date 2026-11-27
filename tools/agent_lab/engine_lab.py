"""成熟Agent内核的独立合同实验；不会加载生产配置或执行业务动作。"""
from __future__ import annotations

import argparse
import openai
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager, closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError
from openai import DefaultAsyncHttpxClient, DefaultHttpxClient
from pydantic import BaseModel, ConfigDict, Field


class ProtocolFailure(RuntimeError):
    """协议异常只暴露分类，不携带供应商正文。"""


class ProtocolGuard(AgentMiddleware):
    """在模型结果进入检查点前区分非法调用、拒答和空正文。"""

    async def awrap_model_call(self, request, handler):
        response = await handler(request)
        for message in response.result:
            if not isinstance(message, AIMessage):
                continue
            if message.invalid_tool_calls:
                raise ProtocolFailure("invalid_tool_json")
            if message.additional_kwargs.get("refusal") or message.response_metadata.get("finish_reason") == "content_filter":
                raise ProtocolFailure("upstream_refusal")
            if not message.content and not message.tool_calls:
                category = "output_budget" if message.response_metadata.get("finish_reason") == "length" else "empty_content"
                raise ProtocolFailure(category)
        return response


class JobArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    job_id: str = Field(min_length=1, max_length=64)


class AssetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    asset_id: str = Field(min_length=1, max_length=64)


class Fixture:
    """使用真实HTTP与SDK解析；响应为受控协议数据，不冒充真实模型。"""

    def __init__(self):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                scenario = self.path.split("/")[2]
                outer.requests.append({"scenario": scenario, "body": body})
                if scenario == "timeout":
                    time.sleep(0.4)
                status = int(scenario[4:]) if scenario.startswith("http") else 200
                if status != 200:
                    value = {"error": {"message": "fixture-only", "type": "fixture_error"}}
                else:
                    message, reason = outer.reply(scenario, body["messages"])
                    value = {"id": "fixture-completion", "object": "chat.completion", "created": 1,
                             "model": "protocol-fixture", "choices": [{"index": 0, "message": message, "finish_reason": reason}],
                             "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except BrokenPipeError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    @staticmethod
    def reply(scenario, messages):
        def call(name, arguments, call_id):
            return {"role": "assistant", "content": None,
                    "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}]}, "tool_calls"
        tool_messages = [m for m in messages if m["role"] == "tool"]
        if scenario == "invalidjson":
            return call("lookup_job", "{", "bad-json")
        if scenario == "refusal":
            return {"role": "assistant", "content": None, "refusal": "fixture-refusal"}, "stop"
        if scenario in {"empty", "budget"}:
            return {"role": "assistant", "content": None}, "length" if scenario == "budget" else "stop"
        if scenario == "loop":
            return call("lookup_job", '{"job_id":"fixture-job"}', f"loop-{len(tool_messages)}")
        if not tool_messages:
            if scenario == "unknown":
                return call("execute_shell", '{"command":"fixture-only"}', "unknown-tool")
            if scenario == "invalidargs":
                return call("lookup_job", '{"job_id":42,"extra":"not-allowed"}', "invalid-args")
            if scenario == "slow":
                return call("slow_read", '{"job_id":"fixture-job"}', "slow-call")
            return call("lookup_job", '{"job_id":"fixture-job"}', "job-call")
        if scenario in {"unknown", "invalidargs"}:
            return {"role": "assistant", "content": "tool-error-observed"}, "stop"
        if len(tool_messages) == 1 and scenario in {"multi", "restart"}:
            return call("lookup_asset", '{"asset_id":"fixture-asset"}', "asset-call")
        return {"role": "assistant", "content": "asset-ready"}, "stop"


def journal_event(path, name):
    with closing(sqlite3.connect(path, timeout=5)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS effects(name TEXT)")
        conn.execute("INSERT INTO effects(name) VALUES(?)", (name,))


def journal_counts(path):
    if not Path(path).exists():
        return {}
    with closing(sqlite3.connect(path)) as conn:
        return dict(conn.execute("SELECT name,count(*) FROM effects GROUP BY name"))


def tools_for(journal, started=None):
    async def lookup_job(job_id):
        journal_event(journal, "lookup_job")
        return json.dumps({"job_id": job_id, "asset_id": "fixture-asset"})

    async def lookup_asset(asset_id):
        journal_event(journal, "lookup_asset")
        return json.dumps({"asset_id": asset_id, "status": "ready"})

    async def slow_read(job_id):
        if started:
            started.set()
        await asyncio.sleep(60)
        journal_event(journal, "slow_read")
        return "finished"

    return [
        StructuredTool.from_function(coroutine=lookup_job, name="lookup_job", description="查询测试任务与关联作品。", args_schema=JobArgs),
        StructuredTool.from_function(coroutine=lookup_asset, name="lookup_asset", description="查询测试作品。", args_schema=AssetArgs),
        StructuredTool.from_function(coroutine=slow_read, name="slow_read", description="用于测试取消的只读等待。", args_schema=JobArgs),
    ]


@asynccontextmanager
async def engine(fixture, scenario, db, journal, *, before=None, after=None, started=None):
    sync_client = DefaultHttpxClient(trust_env=False, follow_redirects=False)
    async_client = DefaultAsyncHttpxClient(trust_env=False, follow_redirects=False)
    try:
        model = ChatOpenAI(
            model="protocol-fixture", api_key="lab-only", base_url=f"http://127.0.0.1:{fixture.server.server_port}/v1/{scenario}",
            temperature=0, max_tokens=512, max_retries=0,
            timeout=0.05 if scenario == "timeout" else 5, use_responses_api=False,
            http_client=sync_client, http_async_client=async_client,
        )
        async with AsyncSqliteSaver.from_conn_string(str(db)) as saver:
            agent = create_agent(model, tools_for(journal, started), system_prompt="只根据工具结果回答。",
                                 middleware=[ProtocolGuard()], checkpointer=saver,
                                 interrupt_before=before, interrupt_after=after)
            yield agent
    finally:
        await async_client.aclose()
        sync_client.close()


def run_config(thread="fixture-thread", limit=16):
    return {"configurable": {"thread_id": thread}, "recursion_limit": limit}


async def checkpoint_phase(root, phase):
    fixture = Fixture()
    try:
        async with engine(fixture, "restart", root / "checkpoints.db", root / "effects.db",
                          after=["tools"] if phase == "pause_after" else None,
                          before=["tools"] if phase == "pause_before" else None) as agent:
            state = await agent.aget_state(run_config())
            value = {"messages": [{"role": "user", "content": "先查任务，再查作品。"}]} if phase.startswith("pause") else None
            result = await agent.ainvoke(value, run_config())
            state = await agent.aget_state(run_config())
            return {"phase": phase, "next": list(state.next), "tool_counts": journal_counts(root / "effects.db"),
                    "http_requests": len(fixture.requests), "final": result["messages"][-1].content}
    finally:
        fixture.close()


async def validate(root):
    results = {}
    fixture = Fixture()
    try:
        for scenario in ["multi", "invalidargs", "unknown"]:
            directory = root / scenario
            directory.mkdir()
            async with engine(fixture, scenario, directory / "checkpoints.db", directory / "effects.db") as agent:
                result = await agent.ainvoke({"messages": [{"role": "user", "content": "查任务和作品。"}]}, run_config())
                counts = journal_counts(directory / "effects.db")
                if scenario == "multi":
                    assert counts == {"lookup_job": 1, "lookup_asset": 1}, counts
                    assert result["messages"][-1].content == "asset-ready"
                else:
                    assert not counts, counts
                    assert result["messages"][-1].content == "tool-error-observed"
                    assert any(getattr(m, "status", None) == "error" for m in result["messages"]), "缺少工具错误反馈"
                results[scenario] = {"passed": True, "tool_counts": counts}
        for scenario, expected in [("invalidjson", "invalid_tool_json"), ("refusal", "upstream_refusal"), ("empty", "empty_content"), ("budget", "output_budget")]:
            directory = root / scenario
            directory.mkdir()
            async with engine(fixture, scenario, directory / "checkpoints.db", directory / "effects.db") as agent:
                try:
                    await agent.ainvoke({"messages": [{"role": "user", "content": "test"}]}, run_config())
                    raise AssertionError("协议异常未分类")
                except ProtocolFailure as exc:
                    assert str(exc) == expected
                    results[scenario] = {"passed": True, "category": str(exc)}
        for scenario, expected in [("http401", "AuthenticationError"), ("http429", "RateLimitError"), ("http503", "InternalServerError"), ("timeout", "APITimeoutError"), ("loop", "GraphRecursionError")]:
            directory = root / scenario
            directory.mkdir()
            before_count = len(fixture.requests)
            async with engine(fixture, scenario, directory / "checkpoints.db", directory / "effects.db") as agent:
                try:
                    await agent.ainvoke({"messages": [{"role": "user", "content": "test"}]}, run_config(limit=6))
                    raise AssertionError("错误或预算未生效")
                except Exception as exc:
                    assert isinstance(exc, GraphRecursionError if expected == "GraphRecursionError" else getattr(openai, expected)), type(exc).__name__
                    count = len(fixture.requests) - before_count
                    assert count <= (3 if scenario == "loop" else 1), count
                    results[scenario] = {"passed": True, "category": expected, "http_requests": count}
        directory = root / "cancel"
        directory.mkdir()
        started = asyncio.Event()
        async with engine(fixture, "slow", directory / "checkpoints.db", directory / "effects.db", started=started) as agent:
            task = asyncio.create_task(agent.ainvoke({"messages": [{"role": "user", "content": "test"}]}, run_config()))
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            try:
                await task
                raise AssertionError("未传播取消")
            except asyncio.CancelledError:
                assert not journal_counts(directory / "effects.db")
                results["cancel"] = {"passed": True, "tool_effects": 0}
        directory = root / "isolation"
        directory.mkdir()
        async with engine(fixture, "multi", directory / "checkpoints.db", directory / "effects.db") as agent:
            await agent.ainvoke({"messages": [{"role": "user", "content": "thread-one"}]}, run_config("one"))
            state = await agent.aget_state(run_config("two"))
            assert not state.values
            results["thread_isolation"] = {"passed": True}
    finally:
        fixture.close()
    for mode in ["before", "after"]:
        directory = root / ("restart_" + mode)
        directory.mkdir()
        def child(phase):
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--phase", phase, "--root", str(directory)], text=True, capture_output=True, timeout=30)
            assert result.returncode == 0, result.stderr[-1000:]
            return json.loads(result.stdout)
        paused = await asyncio.to_thread(child, "pause_" + mode)
        resumed = await asyncio.to_thread(child, "resume")
        replayed = await asyncio.to_thread(child, "resume")
        assert paused["next"], paused
        assert not resumed["next"], resumed
        assert resumed["tool_counts"] == {"lookup_job": 1, "lookup_asset": 1}, resumed
        assert replayed["tool_counts"] == resumed["tool_counts"] and replayed["http_requests"] == 0, replayed
        results["restart_" + mode] = {"passed": True, "paused": paused, "resumed": resumed, "terminal_replay": replayed}
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--phase", default="validate")
    args = parser.parse_args()
    if args.phase != "validate":
        print(json.dumps(asyncio.run(checkpoint_phase(args.root, args.phase))))
    else:
        args.root.mkdir(parents=True, exist_ok=True)
        report = asyncio.run(validate(args.root))
        print(json.dumps({"scope": "真实框架+SDK+受控HTTP，不是实际模型", "cases": report, "passed": len(report)}, ensure_ascii=False, indent=2))
