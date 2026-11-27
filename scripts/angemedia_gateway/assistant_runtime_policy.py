"""运行接入的共同配置判断；事务内授权和服务可用性使用同一规则。"""
from __future__ import annotations

import os


def admission_state(conn) -> str:
    def setting(key):
        row = conn.execute('SELECT value FROM config WHERE key=?', (key,)).fetchone()
        return str(row[0] if row is not None else os.getenv(key, '')).strip()
    if setting('ANGE_ASSISTANT_ENABLED').lower() not in {'1', 'true', 'yes', 'on'}:
        return 'assistant_disabled'
    if not setting('ANGE_LLM_BASE_URL').rstrip('/') or not setting('ANGE_LLM_MODEL'):
        return 'assistant_not_configured'
    return 'running'
