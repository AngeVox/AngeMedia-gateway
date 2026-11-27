"""独立测量导入成本和并发边界，不对应用会话权限作任何假设。"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
from pathlib import Path

from engine_lab import Fixture, engine, journal_counts, run_config


def import_measurement(python, with_agent):
    code = """
import json,resource,time,pathlib
t=time.perf_counter()
import fastapi,httpx,pydantic
"""
    if with_agent:
        code += "from langchain.agents import create_agent\nfrom langchain_openai import ChatOpenAI\nfrom langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver\n"
    code += "status=dict(line.split(':',1) for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line)\n"
    code += "print(json.dumps({'import_seconds':time.perf_counter()-t,'rss_kib':int(status['VmRSS'].split()[0]),'hwm_kib':int(status['VmHWM'].split()[0]),'rusage_max_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))"
    rows = [json.loads(subprocess.check_output([python, "-c", code], text=True, timeout=30)) for _ in range(3)]
    return {"samples": rows, "median_import_seconds": statistics.median(row['import_seconds'] for row in rows),
            "median_rss_kib": statistics.median(row['rss_kib'] for row in rows),
            "median_hwm_kib": statistics.median(row['hwm_kib'] for row in rows)}


def environment(python):
    code = """
import importlib.metadata,json,pathlib,sys,sysconfig
packages={d.metadata['Name'].lower().replace('_','-'):d.version for d in importlib.metadata.distributions()}
root=pathlib.Path(sysconfig.get_paths()['purelib'])
size=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
print(json.dumps({'packages':packages,'site_packages_bytes':size,'python':sys.version.split()[0]}))
"""
    return json.loads(subprocess.check_output([python, "-c", code], text=True, timeout=30))


async def concurrent_probe(root):
    fixture = Fixture()
    try:
        isolated = root / "different-threads"
        isolated.mkdir()
        async with engine(fixture, "multi", isolated / "checkpoints.db", isolated / "effects.db") as agent:
            await asyncio.gather(*[
                agent.ainvoke({"messages": [{"role": "user", "content": f"独立会话{i}"}]}, run_config(f"thread-{i}"))
                for i in range(4)
            ])
            for i in range(4):
                state = await agent.aget_state(run_config(f"thread-{i}"))
                users = [message.content for message in state.values['messages'] if message.type == 'human']
                assert users == [f"独立会话{i}"], users
            counts = journal_counts(isolated / 'effects.db')
            assert counts == {'lookup_job': 4, 'lookup_asset': 4}, counts
        shared = root / "same-thread"
        shared.mkdir()
        async with engine(fixture, "multi", shared / "checkpoints.db", shared / "effects.db") as first:
            async with engine(fixture, "multi", shared / "checkpoints.db", shared / "effects.db") as second:
                results = await asyncio.gather(
                    first.ainvoke({'messages': [{'role': 'user', 'content': '同一会话请求A'}]}, run_config()),
                    second.ainvoke({'messages': [{'role': 'user', 'content': '同一会话请求B'}]}, run_config()),
                    return_exceptions=True,
                )
                outcomes = ['completed' if not isinstance(value, BaseException) else type(value).__name__ for value in results]
                state = await first.aget_state(run_config())
                users = [m.content for m in state.values.get('messages', []) if m.type == 'human']
        return {'different_threads': {'passed': True, 'threads': 4, 'tool_counts': counts},
                'same_thread_without_application_lease': {'outcomes': outcomes, 'retained_user_messages': len(users),
                    'tool_counts': journal_counts(shared / 'effects.db'), 'application_session_lease_required': True,
                    'scope': '观察原生框架行为，不将此项当作应用并发验收通过'}}
    finally:
        fixture.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--baseline-python', required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    base = environment(args.baseline_python)
    current = environment(sys.executable)
    added = {name: version for name, version in current['packages'].items() if name not in base['packages']}
    changed = {name: [version, current['packages'][name]] for name, version in base['packages'].items() if name in current['packages'] and current['packages'][name] != version}
    report = {'baseline_python': base['python'], 'candidate_python': current['python'],
              'added_packages': added, 'changed_baseline_packages': changed,
              'baseline_site_packages_bytes': base['site_packages_bytes'], 'candidate_site_packages_bytes': current['site_packages_bytes'],
              'baseline_import': import_measurement(args.baseline_python, False),
              'candidate_import': import_measurement(sys.executable, True),
              'measurement_scope': 'Linux新进程三次导入；RSS/HWM取/proc/self/status，rusage仅作辅助，防止父进程继承峰值误导；未清理系统文件缓存，不代表整机冷启动或完整应用',
              'concurrency': asyncio.run(concurrent_probe(args.root))}
    print(json.dumps(report, ensure_ascii=False, indent=2))
