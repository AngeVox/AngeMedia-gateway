"""单次媒体调用的私有快照；上下文隔离，结束后释放，不改写全局渠道配置。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy


class MediaExecutionRejected(RuntimeError):
    human_hint = '动作的执行条件已变化，请重新检查并确认；不会自动重新提交。'

    def __init__(self, code='action_execution_rejected'):
        self.code = code
        super().__init__(code)


_CURRENT = ContextVar('media_execution_scope', default=None)


class MediaExecutionSnapshot:
    def __init__(self, *, targets, runtimes, custom, references, relay, transports, validate, operation):
        self._targets = frozenset(targets)
        self._runtimes, self._custom = dict(runtimes), deepcopy(custom)
        self._references, self._relay = dict(references), deepcopy(relay)
        self._transports = dict(transports)
        self._validate = validate
        self._operation = operation
        self._closed = False
        self._guards = []

    def require_target(self, provider, model=None, operation='submit'):
        choices = [target for target in self._targets if target[0] == provider and (model is None or target[1] == model)]
        if self._closed or len(choices) != 1 or operation != self._operation:
            raise MediaExecutionRejected('action_execution_target_changed')
        try:
            self._validate()
            for guard in tuple(self._guards):
                guard()
        except MediaExecutionRejected:
            raise
        except Exception:
            raise MediaExecutionRejected('action_execution_validation_failed') from None

    def _get(self, mapping, key):
        if self._closed or key not in mapping:
            raise MediaExecutionRejected('action_execution_scope_violation')
        return mapping[key]

    def runtime(self, provider):
        return self._get(self._runtimes, provider)

    def custom_provider(self, provider):
        return deepcopy(self._get(self._custom, provider))

    def transport(self, provider):
        return self._get(self._transports, provider)

    def reference(self, locator):
        reference = self._get(self._references, locator)
        try:
            return reference.data_url()
        except (OSError, ValueError):
            raise MediaExecutionRejected('action_execution_reference_changed') from None

    def relay_config(self):
        if self._closed or self._relay is None:
            raise MediaExecutionRejected('action_execution_scope_violation')
        return deepcopy(self._relay)

    @contextmanager
    def validation_guard(self, guard):
        """同一次调用叠加独立证据校验，不覆盖原授权或创建嵌套快照。"""
        if self._closed:
            raise MediaExecutionRejected('action_execution_scope_violation')
        self._guards.append(guard)
        try:
            yield
        finally:
            if guard in self._guards:
                self._guards.remove(guard)

    def close(self):
        self._closed = True
        self._runtimes.clear(); self._custom.clear(); self._references.clear()
        self._transports.clear()
        self._guards.clear()
        self._relay = self._validate = None


def current_execution_scope():
    return _CURRENT.get()


def require_execution_target(provider, model=None, *, operation='submit'):
    scope = current_execution_scope()
    if scope is not None:
        scope.require_target(provider, model, operation)


@contextmanager
def media_execution_scope(snapshot):
    if _CURRENT.get() is not None:
        raise MediaExecutionRejected('action_execution_scope_nested')
    token = _CURRENT.set(snapshot)
    try:
        yield
    finally:
        _CURRENT.reset(token)
        snapshot.close()
