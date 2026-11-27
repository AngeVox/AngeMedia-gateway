"""人类动作请求的会话绑定CSRF证明；仅交给浏览器，不进入模型和提案正文。"""
from __future__ import annotations

from contextlib import closing
import hmac
import re
from urllib.parse import urlsplit

from fastapi import HTTPException

from ..db.connection import db_connect
from ..assistant_run_access import authorize_admin_access
from ..repositories.assistant_action_execution import read_binding_key

HEADER = 'X-AngeMedia-Action-CSRF'


def action_csrf_token(path, access):
    with closing(db_connect(path=path)) as conn:
        authorize_admin_access(conn, access)
        key = read_binding_key(conn)
    return hmac.digest(key, ('human-media-action-csrf-v1:' + access.session_hash).encode('utf-8'), 'sha256').hex()


def require_action_csrf(request, expected):
    def origin(value):
        parsed = urlsplit(value)
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)
    try:
        valid_origin = not request.headers.get('origin') or origin(request.headers['origin']) == origin(str(request.base_url))
    except ValueError:
        valid_origin = False
    provided = request.headers.get(HEADER, '')
    if (request.headers.get('sec-fetch-site') == 'cross-site' or not valid_origin
            or not re.fullmatch(r'[a-f0-9]{64}', provided) or not hmac.compare_digest(provided, expected)):
        raise HTTPException(status_code=403, detail={'code': 'action_csrf_invalid'})
