"""在.2独立环境复用现有助手配置，真实框架多步工具及进程恢复验证。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from engine_lab import ProtocolGuard, journal_counts, journal_event
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from openai import DefaultAsyncHttpxClient, DefaultHttpxClient
from pydantic import BaseModel, ConfigDict, Field


def read_runtime(env_file, db_file):
    """只读凭据留在本进程，不导出环境变量或写入报告。"""
    values = {}
    for line in env_file.read_text().splitlines():
        line = line.strip().removeprefix('export ')
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        parts = shlex.split(value, comments=True)
        values[name.strip()] = parts[0] if parts else ''
    with closing(sqlite3.connect(db_file.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        values.update(dict(conn.execute('SELECT key,value FROM config')))
    return {name: values.get(name, '') for name in ['ANGE_LLM_API_KEY', 'ANGE_LLM_BASE_URL', 'ANGE_LLM_MODEL']}


class TaskArgs(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    task: str = Field(description='固定使用lab-task', min_length=1, max_length=64)


class OutputArgs(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    asset_id: str = Field(description='必须使用lookup_test_task返回的asset_id', min_length=1, max_length=128)


async def run(root, phase, env_file, db_file):
    root.mkdir(parents=True, exist_ok=True)
    fixture_path = root / 'fixture.json'
    if phase == 'pause':
        if fixture_path.exists():
            raise RuntimeError('test_fixture_already_exists')
        fixture_path.write_text(json.dumps({'asset_id': 'asset-' + uuid.uuid4().hex, 'verification': 'verified-' + uuid.uuid4().hex}), encoding='utf-8')
    fixture = json.loads(fixture_path.read_text())
    journal = root / 'tool-journal.db'

    async def task_lookup(task):
        if task != 'lab-task':
            raise ValueError('invalid_test_task')
        journal_event(journal, 'lookup_test_task')
        return json.dumps({'task': task, 'asset_id': fixture['asset_id']})

    async def output_lookup(asset_id):
        if asset_id != fixture['asset_id']:
            raise ValueError('asset_must_come_from_task_tool')
        journal_event(journal, 'lookup_test_output')
        return json.dumps({'asset_id': asset_id, 'verification': fixture['verification']})

    tools = [
        StructuredTool.from_function(coroutine=task_lookup, name='lookup_test_task', description='查询独立测试任务的关联作品ID。', args_schema=TaskArgs),
        StructuredTool.from_function(coroutine=output_lookup, name='lookup_test_output', description='查询第一步返回的作品ID，取得最终验证字符串。', args_schema=OutputArgs),
    ]
    runtime = read_runtime(env_file, db_file)
    if not runtime['ANGE_LLM_BASE_URL'] or not runtime['ANGE_LLM_MODEL']:
        raise RuntimeError('assistant_not_configured')
    sync_client = DefaultHttpxClient(trust_env=False, follow_redirects=False)
    async_client = DefaultAsyncHttpxClient(trust_env=False, follow_redirects=False)
    request_count = 0

    async def count_request(_request):
        nonlocal request_count
        request_count += 1
    async_client.event_hooks['request'] = [count_request]
    try:
        model = ChatOpenAI(model=runtime['ANGE_LLM_MODEL'], api_key=runtime['ANGE_LLM_API_KEY'] or 'unused',
                           base_url=runtime['ANGE_LLM_BASE_URL'], max_tokens=2048, temperature=0,
                           max_retries=0, timeout=45, use_responses_api=False,
                           http_client=sync_client, http_async_client=async_client)
        async with AsyncSqliteSaver.from_conn_string(str(root / 'checkpoints.db')) as saver:
            agent = create_agent(model, tools, middleware=[ProtocolGuard(), ModelCallLimitMiddleware(run_limit=3, exit_behavior='error')],
                                 system_prompt='这是工具协议集成测试，不是媒体生成。先调用lookup_test_task查询lab-task；再用返回的asset_id调用lookup_test_output。不要猜测任何ID。最后只返回第二个工具结果里的verification字符串，不要其他内容。',
                                 checkpointer=saver, interrupt_after=['tools'] if phase == 'pause' else None)
            config = {'configurable': {'thread_id': 'live-test'}, 'recursion_limit': 12}
            result = await agent.ainvoke({'messages': [{'role': 'user', 'content': '查询lab-task并返回最终验证字符串。'}]} if phase == 'pause' else None, config)
            state = await agent.aget_state(config)
            final = result['messages'][-1].content
            counts = journal_counts(journal)
            if phase == 'pause':
                assert counts == {'lookup_test_task': 1} and state.next, 'pause_contract_failed'
            else:
                assert counts == {'lookup_test_task': 1, 'lookup_test_output': 1} and not state.next, 'resume_contract_failed'
                assert isinstance(final, str) and fixture['verification'] in final, 'verification_not_returned'
                if phase == 'replay':
                    assert request_count == 0, 'terminal_replay_called_model'
            return {'phase': phase, 'passed': True, 'captured_at': datetime.now(timezone.utc).isoformat(),
                    'python': sys.version.split()[0], 'http_requests': request_count, 'tool_counts': counts,
                    'pending_nodes': list(state.next), 'verification_matches': phase != 'pause',
                    'business_actions': 0, 'scope': '实际模型+LangChain/LangGraph+独立SQLite；测试工具不读取业务记录'}
    finally:
        await async_client.aclose()
        sync_client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--phase', choices=['pause', 'resume', 'replay'], required=True)
    parser.add_argument('--runtime-env', type=Path, required=True)
    parser.add_argument('--runtime-db', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args.root, args.phase, args.runtime_env, args.runtime_db)), ensure_ascii=True))
    except Exception as exc:
        print(json.dumps({'phase': args.phase, 'passed': False, 'error_type': type(exc).__name__, 'business_actions': 0}))
        sys.exit(1)
