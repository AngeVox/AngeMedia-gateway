"""管理员审阅写入和同快照只读投影。审阅记录不参与恢复授权或发布判定。"""
from collections import Counter
import hashlib
import re

from .. import config as C
from ..assistant_run_access import authorize_admin_access
from ..db.connection import db_transaction, readonly_snapshot
from ..helpers import now_iso
from ..job_submission import JobSubmissionKey, canonical_submission_json
from .legacy_job_evidence import LegacyReviewError, collect_legacy_job_evidence
from .video_upgrade_inventory import job_reference

_LEDGER_COLUMNS = {'sequence', 'request_key', 'job_id', 'evidence_digest',
                   'evidence_json', 'reviewer', 'recorded_at'}
_DIGEST = re.compile(r'[a-f0-9]{64}')


def _ledger_ready(conn):
    columns = {row[1] for row in conn.execute('PRAGMA table_info(legacy_job_reviews)')}
    return _LEDGER_COLUMNS <= columns


def _digest(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _capture(conn, job_id):
    facts = collect_legacy_job_evidence(conn, job_id)
    raw = canonical_submission_json(facts)
    return raw, {'evidence_digest': _digest(raw), 'conclusion': facts['conclusion'],
                 'http_status': facts['http_status'], 'source': facts['source']}


def _observation(row, current, proposal, evidence_state):
    raw = row['evidence_json']
    if not isinstance(raw, str) or _digest(raw) != row['evidence_digest']:
        return {'state': 'evidence_changed'}
    if proposal is None:
        return {'state': evidence_state}
    if current != raw:
        return {'state': 'task_changed'}
    return {'state': 'current', 'conclusion': proposal['conclusion'], 'http_status': proposal['http_status']}


def _current_evidence(conn, job_id):
    try:
        raw, proposal = _capture(conn, job_id)
    except LegacyReviewError as exc:
        return None, None, exc
    return raw, proposal, None


def inspect_legacy_job_review(job_id, *, access):
    with readonly_snapshot(C.DB_FILE) as conn:
        authorize_admin_access(conn, access)
        if not _ledger_ready(conn):
            raise LegacyReviewError('review_ledger_unavailable', 503)
        row = conn.execute('SELECT * FROM legacy_job_reviews WHERE job_id=? ORDER BY sequence DESC LIMIT 1',
                           (job_id,)).fetchone()
        raw, proposal, error = _current_evidence(conn, job_id)
        if error and row is None:
            raise error
        state = error.code if error else 'available'
        review = {'state': 'not_recorded'}
        if row:
            review = {**_observation(row, raw, proposal, state),
                      'reviewer': row['reviewer'], 'recorded_at': row['recorded_at']}
        return {'review': review, 'proposal': proposal, 'evidence_state': state}


def record_legacy_job_review(job_id, *, request_key, evidence_digest, access):
    try:
        key = JobSubmissionKey('legacy_job_review', request_key).value
    except (TypeError, ValueError) as exc:
        raise LegacyReviewError('invalid_review_request_key', 422) from exc
    if not isinstance(evidence_digest, str) or not _DIGEST.fullmatch(evidence_digest):
        raise LegacyReviewError('invalid_review_evidence_digest', 422)
    with db_transaction(immediate=True) as conn:
        # 更名、注销和审阅按同一主库写事务排序。记录使用审阅时的用户名。
        authorize_admin_access(conn, access)
        if not _ledger_ready(conn):
            raise LegacyReviewError('review_ledger_unavailable', 503)
        existing = conn.execute('SELECT * FROM legacy_job_reviews WHERE request_key=?', (key,)).fetchone()
        if existing:
            if (existing['job_id'], existing['evidence_digest'], existing['reviewer']) != (
                    job_id, evidence_digest, access.username):
                raise LegacyReviewError('review_request_conflict')
            return {'sequence': existing['sequence'], 'replayed': True}
        raw, proposal = _capture(conn, job_id)
        if proposal['evidence_digest'] != evidence_digest:
            raise LegacyReviewError('review_evidence_stale')
        cursor = conn.execute(
            'INSERT INTO legacy_job_reviews(request_key,job_id,evidence_digest,evidence_json,reviewer,recorded_at) '
            'VALUES(?,?,?,?,?,?)', (key, job_id, evidence_digest, raw, access.username, now_iso()))
        return {'sequence': cursor.lastrowid, 'replayed': False}


def scan_legacy_job_reviews(conn, detail_limit):
    """在预检快照中按任务读取最近一次审阅，明细上限独立于原阻塞明细。"""
    if not conn.in_transaction or conn.execute('PRAGMA query_only').fetchone()[0] != 1:
        raise ValueError('legacy_job_reviews_require_readonly_snapshot')
    if not _ledger_ready(conn):
        return {'state': 'unavailable', 'reviewed_jobs': None, 'state_counts': {}, 'details': [], 'details_omitted': None}
    rows = conn.execute(
        'SELECT r.* FROM legacy_job_reviews r JOIN '
        '(SELECT MAX(sequence) AS sequence FROM legacy_job_reviews GROUP BY job_id) latest '
        'ON latest.sequence=r.sequence ORDER BY r.job_id')
    counts, details = Counter(), []
    for row in rows:
        raw, proposal, error = _current_evidence(conn, row['job_id'])
        observed = _observation(row, raw, proposal, error.code if error else 'available')
        counts[observed['state']] += 1
        if len(details) < detail_limit:
            details.append({'job_id': job_reference(row['job_id']), **observed})
    total = sum(counts.values())
    return {'state': 'available', 'reviewed_jobs': total, 'state_counts': dict(counts),
            'details': details, 'details_omitted': total - len(details)}
