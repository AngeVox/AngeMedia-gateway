"""把已认证的管理会话转换成数据库复核需要的请求主体。"""
from typing import Annotated

from fastapi import Cookie, Depends

from ..assistant_run_access import RunAccess
from ..runtime import require_admin_auth
from ..security import hash_token


def admin_session_access(principal=Depends(require_admin_auth), am_admin_session: str = Cookie("")) -> RunAccess:
    return RunAccess(principal["username"], hash_token(am_admin_session))


AdminSessionAccess = Annotated[RunAccess, Depends(admin_session_access)]
