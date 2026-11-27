"""准备阶段的私有环境观察；摘要不进入公开提案，不持有数据库写锁读取媒体。"""
from __future__ import annotations

import os
from pathlib import Path

from .. import config as C
from ..job_submission import submission_intent_hash
from ..providers.catalog.loader import CATALOG_DIR, MODELS_FILE, PROVIDERS_FILE


def _value(value):
    return str(value) if isinstance(value, Path) else value


def environment_stamp(conn):
    # 包含秘密的原值只参与进程内摘要，绝不保存或公开摘要输入。
    keys = sorted(C.CONFIG_KEYS | C.SECRET_KEYS)
    data = {'saved': [tuple(row) for row in conn.execute('SELECT key,value FROM config ORDER BY key')],
            'runtime': {key: _value(getattr(C, key, None)) for key in keys},
            'environment': {key: os.environ.get(key) for key in keys},
            'smoke': os.environ.get('QUEUE_SMOKE_FAKE_PROVIDERS'),
            'storage': [str(C.OUTPUT_DIR.resolve()), str(C.UPLOAD_DIR.resolve())]}
    for table in ('custom_providers', 'provider_runtime_configs'):
        data[table] = [dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')]
    data['binding_key'] = [tuple(row) for row in conn.execute('SELECT id,value FROM assistant_action_binding_key')]
    data['catalog'] = [(name, (CATALOG_DIR / name).stat().st_size, (CATALOG_DIR / name).stat().st_mtime_ns)
                       for name in (MODELS_FILE, PROVIDERS_FILE)]
    return submission_intent_hash(data)
