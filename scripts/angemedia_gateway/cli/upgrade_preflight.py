"""读取显式数据库的升级总览；0无已记录阻塞、2有阻塞、1无法完整盘点。"""
import argparse
import json

from .preflight_database import PreflightDatabaseOwnerMismatch, preflight_database_path


def _unavailable(error_code='upgrade_inventory_unavailable', **facts):
    return {'schema_version': 1, 'scope': 'local_application_upgrade_evidence', 'scan_complete': False,
            'inventory_gate': 'unavailable', 'deployment_ready': False, 'error_code': error_code, **facts}


def main(argv=None):
    parser = argparse.ArgumentParser(description='只读盘点应用升级证据，不停止、不迁移、不调用渠道、不授权部署。')
    parser.add_argument('--database', required=True, help='明确指定主数据库文件')
    parser.add_argument('--detail-limit', type=int, default=1000, help='明细上限0至10000，完整计数不受上限影响')
    parser.add_argument('--include-legacy-reviews', action='store_true', help='附加旧任务审阅状态，保持现有阻塞判定')
    args = parser.parse_args(argv)
    try:
        database = preflight_database_path(args.database)
        from ..services.upgrade_inventory import UpgradeInventorySchemaError, inventory_application_upgrade
        try:
            report = inventory_application_upgrade(database, detail_limit=args.detail_limit,
                                                   include_legacy_reviews=args.include_legacy_reviews)
        except UpgradeInventorySchemaError as exc:
            # 已知结构缺失可明确诊断，但不能把部分盘点当作完整升级检查。
            print(json.dumps(_unavailable('upgrade_inventory_schema_unsupported', missing_tables=exc.missing_tables,
                                          missing_columns=exc.missing_columns), ensure_ascii=False, sort_keys=True))
            return 1
    except PreflightDatabaseOwnerMismatch:
        print(json.dumps(_unavailable('preflight_database_owner_mismatch')))
        return 1
    except Exception:
        print(json.dumps(_unavailable()))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 2 if report['blocker_count'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
