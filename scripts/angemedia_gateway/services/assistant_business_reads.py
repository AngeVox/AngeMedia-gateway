"""有界业务读模型；复用已有仓库与服务，公开字段不含凭据或原始载荷。"""
from __future__ import annotations

from ..repositories.assets import get_asset, list_assets
from ..repositories.jobs import list_jobs
from ..presenters.assets import controlled_asset_path
from .assistant_tools import AssistantToolNotFound


def read_jobs(args):
    rows = list_jobs(**args)
    items = [{key: row.get(key) for key in ('id', 'kind', 'status', 'stage', 'model', 'provider',
             'created_at', 'updated_at', 'error_code')} for row in rows]
    return {'items': items, 'count': len(items), 'limit': args['limit'], 'offset': args['offset']}, f'检索到{len(items)}个任务'
from .diagnostics_summary import DiagnosticsSummaryService


def read_assets(args):
    if args.get("asset_id"):
        row = get_asset(args["asset_id"])
        if row is None or (args.get("job_id") and row.get("job_id") != args["job_id"]) or (
            args.get("media_type") and row.get("media_type") != args["media_type"]
        ):
            raise AssistantToolNotFound("asset not found")
        rows = [row]
    else:
        rows = list_assets(limit=args["limit"], offset=args["offset"], job_id=args.get("job_id"), media_type=args.get("media_type"))
    items = [{**{key: row.get(key) for key in ("id", "job_id", "media_type", "source", "model", "provider", "size", "duration_ms", "created_at")},
              "url_path": controlled_asset_path(row.get("url_path"))} for row in rows]
    return {"items": items, "count": len(items), "limit": args["limit"], "offset": args["offset"]}, f"检索到{len(items)}个作品"


def read_diagnostics(_args):
    data = DiagnosticsSummaryService().assistant_summary()
    return data, "已读取系统诊断；broker连通性与任务状态按各字段报告，不表示生成渠道已验证"


def read_queue(_args):
    full = DiagnosticsSummaryService().assistant_summary()
    return {"queue": full["queue"], "dispatches": full["dispatches"]}, "已读取队列与投递状态"


def bounded_result(name, args, payload):
    data, summary = payload
    if name == "recent_logs":
        data = {**data, "sources": [{**item, "lines": [line[:240] for line in item["lines"][-args["line_limit"]:]]}
                                    for item in data.get("sources", [])]}
    elif name == "channel_safe_summary":
        data = {**data, "channels": data["channels"][args["offset"]:args["offset"] + args["limit"]],
                "limit": args["limit"], "offset": args["offset"]}
    return data, summary
