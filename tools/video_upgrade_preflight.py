"""读取指定SQLite库并输出任务升级盘点；退出码0无已记录阻塞、2有阻塞、1盘点失败。"""
import argparse
import json
from pathlib import Path
import sys


def _unavailable(error_code='video_inventory_unavailable'):
    print(json.dumps({'schema_version': 1, 'scan_complete': False, 'task_gate': 'unavailable',
                      'deployment_ready': False, 'error_code': error_code}))
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='只读盘点视频任务升级证据，不迁移、不查询渠道、不授权部署。')
    parser.add_argument('--database', required=True, help='明确指定要盘点的SQLite数据库')
    parser.add_argument('--detail-limit', type=int, default=1000, help='明细最多0至10000项，完整计数始终保留')
    arguments = parser.parse_args(argv)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
        from angemedia_gateway.cli.preflight_database import PreflightDatabaseOwnerMismatch, preflight_database_path
        try:
            database = preflight_database_path(arguments.database)
        except PreflightDatabaseOwnerMismatch:
            return _unavailable('preflight_database_owner_mismatch')
        from angemedia_gateway.services.video_upgrade_inventory import inventory_video_upgrade
        result = inventory_video_upgrade(database, detail_limit=arguments.detail_limit)
    except Exception:
        # 异常可能包含路径、正文或环境变量；仅输出固定错误，不打印异常消息和堆栈。
        return _unavailable()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result['blocker_count'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
