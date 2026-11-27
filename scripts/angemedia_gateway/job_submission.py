"""持久提交标识和不可变入队快照；不携带授权，也不调用供应商。"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any
from uuid import UUID


class JobSubmissionConflict(ValueError):
    """同一提交键已经绑定不同意图，不能偷偷改写成另一次生成。"""


class JobSubmissionGone(LookupError):
    """原任务已清理，提交凭据仍有效，不能用旧键再次生成。"""

    def __init__(self, job_id: str):
        super().__init__('原提交任务已清理')
        self.job_id = job_id


def canonical_submission_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def submission_intent_hash(value: Any) -> str:
    return hashlib.sha256(canonical_submission_json(value).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class JobSubmissionKey:
    namespace: str
    value: str

    def __post_init__(self):
        if not isinstance(self.namespace, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', self.namespace):
            raise ValueError('提交命名空间无效')
        if not isinstance(self.value, str) or len(self.value) not in (32, 36):
            raise ValueError('提交键必须是UUID')
        object.__setattr__(self, 'value', UUID(self.value).hex)


@dataclass(frozen=True)
class PreparedJobAdmission:
    _arguments_json: str = field(repr=False)

    @classmethod
    def from_arguments(cls, **arguments) -> 'PreparedJobAdmission':
        return cls(canonical_submission_json(arguments))

    def arguments(self) -> dict[str, Any]:
        # 每次返回独立对象，修改调用参数不会改变已展示/保存的候选快照。
        result = json.loads(self._arguments_json)
        if 'dedupe_hashes' in result:
            result['dedupe_hashes'] = tuple(tuple(item) for item in result['dedupe_hashes'])
        return result
