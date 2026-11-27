"""动作提案、修订、撤回与确认事务；模型不能提供确认凭证，执行仍交现有队列。"""
from __future__ import annotations

import json
from pathlib import Path
import time
from uuid import UUID, uuid5

from .. import config as C
from ..assistant_action_contract import ActionConfirmation, ActionError
from ..assistant_action_access import ProposalRunAccess, authorize_action_access, require_human_access
from ..db.connection import db_transaction
from ..db.readonly_scope import readonly_database
from ..job_submission import JobSubmissionKey, submission_intent_hash
from ..repositories import assistant_actions as repository
from ..repositories.assistant_action_execution import MARKER, save_grant
from .assistant_action_preparation import ActionPreparer
from .assistant_action_views import action_view
from .assistant_business_secrets import configured_secrets, redact_values
from .job_admission import JobAdmissionService
from .job_control import (JobControlError, cancellation_reason, cancel_before_start,
                          validate_job_version, recovery_state, regeneration_control)
from ..repositories.jobs import get_job


class AssistantActionService:
    def __init__(self, *, db_path: Path | None = None, preparer=None, clock=None,
                 ttl_ms=900000, max_pending=256, max_revisions=32):
        if type(ttl_ms) is not int or not 1000 <= ttl_ms <= 3600000:
            raise ValueError('动作有效期须为1000到3600000毫秒')
        if type(max_pending) is not int or not 1 <= max_pending <= 1024 or type(max_revisions) is not int or not 1 <= max_revisions <= 128:
            raise ValueError('动作容量或修订上限无效')
        self.db_path = Path(C.DB_FILE if db_path is None else db_path).resolve()
        self.preparer = preparer or ActionPreparer(self.db_path)
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.ttl_ms, self.max_pending, self.max_revisions = ttl_ms, max_pending, max_revisions

    def _key(self, value):
        try: return JobSubmissionKey('assistant_action', value).value
        except ValueError: raise ActionError('invalid_action_request_key') from None

    def _fingerprint(self, kind, request):
        from ..job_submission import canonical_submission_json
        if kind not in {'image', 'video'} or not isinstance(request, dict): raise ActionError('invalid_action_request')
        try:
            text = canonical_submission_json({'kind': kind, 'request': request})
            if len(text.encode('utf-8')) > 262144: raise ActionError('action_payload_too_large')
            return submission_intent_hash(json.loads(text))
        except (ValueError, TypeError, RecursionError): raise ActionError('invalid_action_request') from None

    def _view(self, conn, action, version=None):
        rev = repository.revision(conn, action['id'], action['version'] if version is None else version)
        result = action_view(conn, action, rev, self.clock())
        with readonly_database(self.db_path):
            if result['review'] is not None and redact_values(result['review'], configured_secrets()) != result['review']:
                result['review'] = None; result['review_withheld'] = True
        return result

    def _draft(self, conn, action, access, version):
        if type(version) is not int or version != action['version']: raise ActionError('action_version_conflict')
        if action['status'] != 'draft': raise ActionError('action_not_draft')
        authorize_action_access(conn, access, action['session_id'])
        revision = repository.revision(conn, action['id'], version)
        if revision['expires_ms'] <= self.clock(): raise ActionError('action_expired')
        if revision['body_json'] is None: raise ActionError('action_revoked')
        return revision

    def _creation_replay(self, conn, action, access, fingerprint):
        action = repository.owned_action(conn, action['id'], access)
        if action['creation_fingerprint'] != fingerprint: raise ActionError('action_request_conflict')
        return {'created': False, 'action': self._view(conn, action)}

    def propose(self, session_id, request_key, kind, request, *, access):
        key = self._key(request_key); fingerprint = self._fingerprint(kind, request)
        with db_transaction(immediate=isinstance(access, ProposalRunAccess), path=self.db_path) as conn:
            authorize_action_access(conn, access, session_id)
            existing = repository.creation(conn, session_id, key)
            if existing is not None: return self._creation_replay(conn, existing, access, fingerprint)
        candidate = self.preparer.prepare(kind, request)
        with db_transaction(immediate=True, path=self.db_path) as conn:
            username = authorize_action_access(conn, access, session_id)
            existing = repository.creation(conn, session_id, key)
            if existing is not None: return self._creation_replay(conn, existing, access, fingerprint)
            if not self.preparer.unchanged(conn, candidate): raise ActionError('action_environment_changed')
            if repository.active_count(conn, username, self.clock()) >= self.max_pending: raise ActionError('action_capacity_reached')
            action_id = uuid5(UUID(session_id), 'action:' + key).hex
            repository.create(conn, action_id, session_id, username, key, fingerprint)
            action = repository.owned_action(conn, action_id, access)
            repository.append_revision(conn, action, key=key, fingerprint=fingerprint, candidate=candidate,
                                       expires_ms=self.clock() + self.ttl_ms)
            return {'created': True, 'action': self._view(conn, action)}

    def inspect(self, action_id, *, access):
        with db_transaction(immediate=isinstance(access, ProposalRunAccess), path=self.db_path) as conn:
            return self._view(conn, repository.owned_action(conn, action_id, access))

    def list(self, session_id, *, access, limit=50, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ActionError('invalid_action_pagination')
        with db_transaction(immediate=isinstance(access, ProposalRunAccess), path=self.db_path) as conn:
            username = authorize_action_access(conn, access, session_id)
            total = conn.execute('SELECT count(*) FROM assistant_actions WHERE session_id=? AND username=?', (session_id, username)).fetchone()[0]
            rows = conn.execute('SELECT * FROM assistant_actions WHERE session_id=? AND username=? ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?',
                                (session_id, username, limit, offset)).fetchall()
            return {'items': [self._view(conn, dict(row)) for row in rows], 'total': total, 'limit': limit, 'offset': offset}

    def revise(self, action_id, expected_version, request_key, kind, request, *, access):
        key = self._key(request_key); fingerprint = self._fingerprint(kind, request)
        with db_transaction(immediate=isinstance(access, ProposalRunAccess), path=self.db_path) as conn:
            action = repository.owned_action(conn, action_id, access)
            existing = repository.revision_request(conn, action_id, key)
            if existing:
                if existing['request_fingerprint'] != fingerprint: raise ActionError('action_request_conflict')
                return {'created': False, 'action': self._view(conn, action, existing['version'])}
            # 过期提案允许显式修订刷新；仍要求最新版本和有效会话，绝不隐式续期。
            if type(expected_version) is not int or expected_version != action['version']: raise ActionError('action_version_conflict')
            if action['status'] != 'draft': raise ActionError('action_not_draft')
            authorize_action_access(conn, access, action['session_id'])
        candidate = self.preparer.prepare(kind, request)
        with db_transaction(immediate=True, path=self.db_path) as conn:
            action = repository.owned_action(conn, action_id, access)
            existing = repository.revision_request(conn, action_id, key)
            if existing:
                if existing['request_fingerprint'] != fingerprint: raise ActionError('action_request_conflict')
                return {'created': False, 'action': self._view(conn, action, existing['version'])}
            if expected_version != action['version']: raise ActionError('action_version_conflict')
            if action['status'] != 'draft': raise ActionError('action_not_draft')
            username = authorize_action_access(conn, access, action['session_id'])
            if action['version'] >= self.max_revisions: raise ActionError('action_revision_limit')
            previous = repository.revision(conn, action_id, action['version'])
            if previous['expires_ms'] <= self.clock() and repository.active_count(conn, username, self.clock()) >= self.max_pending:
                raise ActionError('action_capacity_reached')
            if not self.preparer.unchanged(conn, candidate): raise ActionError('action_environment_changed')
            action['version'] += 1
            repository.append_revision(conn, action, key=key, fingerprint=fingerprint, candidate=candidate,
                                       expires_ms=self.clock() + self.ttl_ms)
            return {'created': True, 'action': self._view(conn, action)}

    def revoke(self, action_id, *, access):
        require_human_access(access)
        with db_transaction(immediate=True, path=self.db_path) as conn:
            action = repository.owned_action(conn, action_id, access)
            if action['status'] == 'submitted': raise ActionError('action_already_submitted')
            repository.revoke(conn, action_id)
            return self._view(conn, repository.owned_action(conn, action_id, access))

    def _check_confirmation(self, action, revision, confirmation):
        if action['version'] != confirmation.version: raise ActionError('action_version_conflict')
        if revision['binding_digest'] != confirmation.binding_digest: raise ActionError('action_binding_conflict')

    def confirm(self, action_id, confirmation: ActionConfirmation, *, access):
        require_human_access(access)
        if not isinstance(confirmation, ActionConfirmation): raise ActionError('invalid_confirmation')
        with db_transaction(path=self.db_path) as conn:
            action = repository.owned_action(conn, action_id, access)
            revision = repository.revision(conn, action_id, action['version'])
            self._check_confirmation(action, revision, confirmation)
            if action['status'] == 'submitted': return {'created': False, 'action': self._view(conn, action)}
            revision = self._draft(conn, action, access, confirmation.version)
            body = json.loads(revision['body_json'])
        candidate = self.preparer.prepare(body['kind'], body['request'])
        current = candidate.body()
        if not current['review']['allowed']: raise ActionError('action_policy_denied')
        if candidate.digest != confirmation.binding_digest: raise ActionError('action_environment_changed')
        if not set(current['review']['required_acknowledgements']) <= confirmation.acknowledgements:
            raise ActionError('action_acknowledgements_required')
        with db_transaction(immediate=True, path=self.db_path) as conn:
            action = repository.owned_action(conn, action_id, access)
            revision = repository.revision(conn, action_id, action['version'])
            self._check_confirmation(action, revision, confirmation)
            if action['status'] == 'submitted': return {'created': False, 'action': self._view(conn, action)}
            self._draft(conn, action, access, confirmation.version)
            if not self.preparer.unchanged(conn, candidate): raise ActionError('action_environment_changed')
            current['admission']['payload'][MARKER] = {'id': action_id, 'version': action['version'], 'digest': candidate.digest}
            result = JobAdmissionService().admit(**current['admission'], conn=conn,
                submission_key=JobSubmissionKey('assistant_action', action_id))
            repository.submit(conn, action_id, result.job['id'], confirmation.acknowledgements)
            save_grant(conn, action, candidate, result.job)
            return {'created': result.created, 'action': self._view(conn, repository.owned_action(conn, action_id, access))}

    def revise_patch(self, action_id, expected_version, request_key, changes, *, access):
        """从服务端不可变基版应用浅层请求补丁，未提及字段和长提示词不被模型摘要替换。"""
        if type(expected_version) is not int or expected_version < 1 or not isinstance(changes, dict) or not changes:
            raise ActionError('invalid_action_request')
        with db_transaction(immediate=isinstance(access, ProposalRunAccess), path=self.db_path) as conn:
            action = repository.owned_action(conn, action_id, access)
            revision = repository.revision(conn, action_id, expected_version)
            if revision['body_json'] is None:
                raise ActionError('action_revoked')
            body = json.loads(revision['body_json'])
            request = {**body['request'], **changes}
        return self.revise(action_id, expected_version, request_key, body['kind'], request, access=access)

    def _job_for_control(self, conn, action_id, access):
        action = repository.owned_action(conn, action_id, access)
        if action['status'] != 'submitted' or not action['job_id']:
            raise JobControlError('action_not_submitted')
        job = get_job(action['job_id'], conn=conn)
        if job is None:
            raise JobControlError('job_record_unavailable')
        return action, job

    def job_control(self, action_id, *, access):
        require_human_access(access)
        with db_transaction(path=self.db_path) as conn:
            action, job = self._job_for_control(conn, action_id, access)
            reason = cancellation_reason(conn, job)
            regenerate = regeneration_control(job)
            revision = repository.revision(conn, action_id, action['version'])
            if revision['body_json'] is None or not conn.execute(
                    "SELECT 1 FROM assistant_sessions WHERE id=? AND status='active'", (action['session_id'],)).fetchone():
                regenerate = {**regenerate, 'enabled': False, 'reason': 'action_source_unavailable'}
            return {'job': {'id': job['id'], 'version': job['version'], 'status': job['status']},
                    'cancel': {'enabled': reason is None, 'reason': reason, 'scope': 'before_start'},
                    'retry': {'enabled': False, 'reason': 'retry_requires_new_proposal'},
                    'recovery': {'state': recovery_state(job), 'automatic_resubmit': False},
                    'regenerate': regenerate}

    def regenerate(self, action_id, expected_version, request_key, *, access):
        """另建待确认草稿；原动作、任务和已消费授权保持不变。"""
        require_human_access(access)
        validate_job_version(expected_version)
        request_key = self._key(request_key)
        with db_transaction(path=self.db_path) as conn:
            action, job = self._job_for_control(conn, action_id, access)
            authorize_action_access(conn, access, action['session_id'])
            if job['version'] != expected_version:
                raise JobControlError('job_version_conflict')
            control = regeneration_control(job)
            if not control['enabled']:
                raise JobControlError(control['reason'])
            revision = repository.revision(conn, action_id, action['version'])
            if revision['body_json'] is None:
                raise JobControlError('action_source_unavailable')
            body = json.loads(revision['body_json'])
        # 源任务已终结，后续只创建草稿；propose再次校验同一会话及当前准备环境。
        key = uuid5(UUID(action_id), 'regenerate:' + request_key).hex
        result = self.propose(action['session_id'], key, body['kind'], body['request'], access=access)
        return {**result, 'source': {'action_id': action_id, 'job_id': job['id'], 'job_version': expected_version}}

    def cancel_job(self, action_id, expected_version, *, access):
        require_human_access(access)
        with db_transaction(immediate=True, path=self.db_path) as conn:
            action, job = self._job_for_control(conn, action_id, access)
            created, _ = cancel_before_start(conn, job, expected_version)
            return {'created': created, 'cancel_scope': 'before_start', 'action': self._view(conn, action)}
