"""复用现有助手配置验证正式适配器；只操作新建隔离库与随机测试工具。"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
from live_engine import read_runtime
from engine_lab import journal_event, journal_counts
import angemedia_gateway.config as C
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.assistant_runtime import AssistantRunRepository
from angemedia_gateway.repositories.assistant_sessions import create_assistant_session
from angemedia_gateway.services.assistant_config_service import AssistantRuntimeConfig
from angemedia_gateway.services.assistant_engine import AssistantEngine
from angemedia_gateway.services.assistant_run_supervisor import AssistantRunSupervisor
import angemedia_gateway.services.assistant_engine as engine_module
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field


class TaskArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task: str = Field(min_length=1, max_length=64)


class AssetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    asset_id: str = Field(min_length=1, max_length=128)


async def run(args):
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    fixture_file = root/"fixture.json"
    if args.phase == "run":
        if fixture_file.exists():
            raise RuntimeError("必须使用新的隔离目录")
        fixture = {"asset_id":"asset-"+uuid4().hex, "verification":"verified-"+uuid4().hex,
                   "session_id":uuid4().hex, "request_key":uuid4().hex}
        fixture_file.write_text(json.dumps(fixture), encoding="utf-8")
    fixture = json.loads(fixture_file.read_text())
    C.DB_FILE = root/"runtime.db"
    init_db()
    if args.phase == "run":
        create_assistant_session(fixture["session_id"], "独立测试")
    journal = root/"effects.db"
    async def lookup_test_task(task):
        if task != "lab-task":
            raise ValueError("invalid_test_task")
        journal_event(journal, "lookup_test_task")
        return json.dumps({"asset_id":fixture["asset_id"]})
    async def lookup_test_output(asset_id):
        if asset_id != fixture["asset_id"]:
            raise ValueError("invalid_asset_id")
        journal_event(journal, "lookup_test_output")
        return json.dumps({"verification":fixture["verification"]})
    tools = [StructuredTool.from_function(coroutine=lookup_test_task, name="lookup_test_task", description="查询独立测试任务lab-task的作品ID。", args_schema=TaskArgs),
             StructuredTool.from_function(coroutine=lookup_test_output, name="lookup_test_output", description="使用上一工具返回的作品ID查最终验证字符串。", args_schema=AssetArgs)]
    values = read_runtime(args.runtime_env, args.runtime_db)
    runtime = AssistantRuntimeConfig(values["ANGE_LLM_BASE_URL"], values["ANGE_LLM_API_KEY"], values["ANGE_LLM_MODEL"], "saved")
    requests = 0
    original = engine_module.outbound_client
    @asynccontextmanager
    async def counted(**kwargs):
        nonlocal requests
        async def count(_request):
            nonlocal requests
            requests += 1
        async with original(**kwargs) as client:
            client.event_hooks["request"].append(count)
            yield client
    engine_module.outbound_client = counted
    repo = AssistantRunRepository()
    prompt = "这是独立测试，不生成媒体。先用lookup_test_task查lab-task，再把返回的asset_id交给lookup_test_output；最后只回复第二步返回的verification，不得猜测ID。"
    admitted = repo.admit(fixture["session_id"], fixture["request_key"], {"message":prompt}, timeout_ms=120000, max_model_calls=4, max_tool_calls=4)
    before = journal_counts(journal)
    try:
        async with AssistantRunSupervisor(repo, AssistantEngine(root/"checkpoints.db", tools, config_resolver=lambda:runtime, model_timeout=45)) as supervisor:
            result = await supervisor.execute(admitted.run["id"])
    finally:
        engine_module.outbound_client = original
    counts = journal_counts(journal)
    from angemedia_gateway.repositories.assistant_sessions import list_assistant_messages
    messages = list_assistant_messages(fixture["session_id"])
    matched = any(fixture["verification"] in row["content"] for row in messages if row["role"] == "assistant")
    report = {"phase":args.phase,"status":result["status"],"http_requests":requests,
              "model_calls":result["model_calls"],"tool_calls":result["tool_calls"],"tool_counts":counts,"verification_matched":matched,
              "business_actions":0,"replay_unchanged":counts==before if args.phase=="replay" else None}
    (root/(args.phase+"-report.json")).write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report))
    assert result["status"] == "succeeded" and matched
    assert counts == {"lookup_test_task":1,"lookup_test_output":1}
    if args.phase == "replay":
        assert requests == 0 and before == counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--phase",choices=("run","replay"),required=True)
    parser.add_argument("--runtime-env",type=Path,required=True)
    parser.add_argument("--runtime-db",type=Path,required=True)
    asyncio.run(run(parser.parse_args()))
