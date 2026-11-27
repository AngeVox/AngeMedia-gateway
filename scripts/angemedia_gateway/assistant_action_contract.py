"""动作域的固定诊断、确认载荷和不可变准备结果；不承接模型授权。"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re

from .job_submission import canonical_submission_json, submission_intent_hash

ACKNOWLEDGEMENTS = frozenset({'may_incur_cost', 'unverified_model', 'mutable_remote_reference'})
PROPOSAL_TOOL_ERRORS = frozenset({'proposal_rejected', 'action_not_found', 'action_version_conflict',
    'action_not_draft', 'action_expired', 'action_revoked', 'action_environment_changed',
    'action_request_conflict', 'action_capacity_reached', 'action_revision_limit', 'invalid_action_request',
    'action_provider_unavailable', 'model_selection_required', 'action_contains_sensitive_value'})


class ActionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ActionConfirmation:
    version: int
    binding_digest: str
    acknowledgements: frozenset[str] = frozenset()

    def __post_init__(self):
        if type(self.version) is not int or self.version < 1 or not isinstance(self.binding_digest, str) or not re.fullmatch(r'[a-f0-9]{64}', self.binding_digest):
            raise ActionError('invalid_confirmation')
        if not isinstance(self.acknowledgements, frozenset) or not self.acknowledgements <= ACKNOWLEDGEMENTS:
            raise ActionError('invalid_confirmation')


@dataclass(frozen=True)
class ActionCandidate:
    _body: str = field(repr=False)
    environment_stamp: str = field(repr=False)
    _checks: str = field(repr=False)

    @classmethod
    def build(cls, body, environment_stamp, checks):
        encoded = canonical_submission_json(body)
        if len(encoded.encode('utf-8')) > 262144:
            raise ActionError('action_payload_too_large')
        return cls(encoded, environment_stamp, canonical_submission_json(checks))

    @property
    def digest(self):
        return submission_intent_hash(self.body())

    def body(self):
        return json.loads(self._body)

    def checks(self):
        return json.loads(self._checks)
