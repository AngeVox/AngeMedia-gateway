"""持久事件SSE传输；浏览器游标绑定运行，订阅不拥有执行任务。"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from ..assistant_run_access import RunAuthenticationExpired, validate_event_cursor
from ..assistant_runtime_contract import TERMINAL_RUN_STATUSES, RunNotFound
from ..repositories.assistant_conversations import ConversationNotFound

LOGGER = logging.getLogger(__name__)


def event_cursor(run_id: str, after: int | None, last_event_id: str | None) -> int:
    if last_event_id:
        match = re.fullmatch(r"([a-f0-9]{32}):([0-9]{1,19})", last_event_id)
        if match is None or match[1] != run_id:
            raise ValueError("Last-Event-ID必须是当前运行ID与事件序号")
        header_after = int(match[2])
        if after is not None and after != header_after:
            raise ValueError("事件游标参数相互冲突")
        after = header_after
    cursor = 0 if after is None else after
    validate_event_cursor(cursor, 100)
    return cursor


def sse_frame(event: str, data, *, event_id: str | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return prefix + f"event: {event}\ndata: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n\n"


async def watch_run(service, run_id, access, initial, *, poll_seconds: float = 0.5):
    """JSON与两种SSE共用观察器；取消观察不取消后台执行。"""
    snapshot = initial
    while not service.stopping.is_set():
        yield snapshot
        if not snapshot['has_more'] and snapshot['run']['status'] in TERMINAL_RUN_STATUSES:
            return
        if not snapshot['has_more']:
            try:
                await asyncio.wait_for(service.stopping.wait(), poll_seconds)
                return
            except asyncio.TimeoutError:
                pass
        snapshot = await service.inspect(run_id, access=access, after=snapshot['next_after'], limit=50)


async def run_event_stream(service, run_id, access, initial, *, poll_seconds: float = 0.5):
    last_sent = asyncio.get_running_loop().time()
    try:
        async for snapshot in watch_run(service, run_id, access, initial, poll_seconds=poll_seconds):
            if snapshot['events']:
                # 一页在同一授权快照内读取并一次交给传输层，不在慢客户端上持有数据库事务。
                yield "".join(sse_frame(item["event_type"], item,
                                       event_id=f"{run_id}:{item['sequence']}") for item in snapshot["events"])
                last_sent = asyncio.get_running_loop().time()
            if not snapshot["has_more"] and snapshot["run"]["status"] in TERMINAL_RUN_STATUSES:
                # done是传输控制消息，不分配持久序号，也不改变运行状态。
                yield sse_frame("done", {"run": snapshot["run"]})
                return
            if not snapshot["events"] and asyncio.get_running_loop().time() - last_sent >= 15:
                yield ": keep-alive\n\n"
                last_sent = asyncio.get_running_loop().time()
    except RunAuthenticationExpired:
        yield sse_frame('error', {'code': 'authentication_expired'})
    except (RunNotFound, ConversationNotFound):
        yield sse_frame('error', {'code': 'run_not_found'})
    except Exception as exc:
        LOGGER.warning('助手事件订阅结束：error_type=%s', type(exc).__name__)
        yield sse_frame('error', {'code': 'run_stream_unavailable'})
