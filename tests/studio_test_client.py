"""显式模拟当前Cookie客户端协议；原始TestClient用于协议负向测试。"""
from urllib.parse import urlsplit, parse_qs

from fastapi.testclient import TestClient as RawTestClient


class TestClient(RawTestClient):
    __test__ = False

    def seed_session(self, token):
        """供无登录路由的仓储夹具显式绑定已创建的会话，不自动拦截Cookie修改。"""
        from angemedia_gateway.security import admin_session_context
        self.cookies.set('am_admin_session', token)
        self._studio_context = admin_session_context(token)

    def request(self, method, url, **kwargs):
        path = urlsplit(str(url)).path
        headers = dict(kwargs.get('headers') or {})
        params = kwargs.get('params') or {}
        query = parse_qs(urlsplit(str(url)).query)
        has_context = any(key.lower() == 'x-studio-session-context' for key in headers)
        has_context = has_context or 'studio_session_context' in params or 'studio_session_context' in query
        context = getattr(self, '_studio_context', None)
        if context and not has_context and path not in ('/v1/admin/login', '/v1/admin/session'):
            headers['X-Studio-Session-Context'] = context
        kwargs['headers'] = headers
        response = super().request(method, url, **kwargs)
        if path == '/v1/admin/login' and response.status_code == 200:
            self._studio_context = response.json()['session_context']
        return response
