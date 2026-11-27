"""为一次提交或取回固定凭据、传输和引用，并复核当前执行身份。"""
from contextlib import ExitStack, closing, contextmanager
import hmac
import json

from ..assistant_action_contract import ActionError
from ..db.connection import db_connect
from ..media_execution_scope import MediaExecutionRejected, MediaExecutionSnapshot, current_execution_scope, media_execution_scope
from ..pollinations_video import POLLINATIONS_VIDEO_MODEL
from ..providers.runtime_config import resolve_live_provider_runtime_config
from ..providers.transport_config import resolve_live_provider_transport
from ..reference_images import freeze_image_references
from ..repositories.assistant_action_execution import job_intent_digest, load_grant
from ..repositories.jobs import get_job
from ..repositories.pollinations_video_receipts import video_receipt
from ..repositories.reference_relay_config import get_live_reference_relay_config
from ..repositories.settings import builtin_provider_enabled
from .assistant_action_execution import action_job_execution
from .assistant_action_references import inspect_references
from .media_connection_binding import connection_binding

PROVIDER = "pollinations"
TARGET = (PROVIDER, POLLINATIONS_VIDEO_MODEL)


def validate_video_ownership(conn, job, receipt):
    current = get_job(job["id"], conn=conn)
    if current is None or current["version"] != job["version"] or current["status"] != "running":
        raise MediaExecutionRejected("pollinations_video_job_changed")
    if (receipt is None or receipt != video_receipt(conn, job["id"])
        or job_intent_digest(current) != receipt["intent_digest"]
        or (job["stage"] != "video_submit" and current["external_task_id"] != receipt["task_id"])):
        raise MediaExecutionRejected("pollinations_video_receipt_invalid")
    load_grant(conn, current)
    return current


@contextmanager
def pollinations_video_execution(job, receipt, *, phase):
    with action_job_execution(job, phase=phase, target=TARGET), ExitStack() as resources:
        inherited = current_execution_scope()
        runtime = inherited.runtime(PROVIDER) if inherited else resolve_live_provider_runtime_config(PROVIDER)
        transport = inherited.transport(PROVIDER) if inherited else resolve_live_provider_transport(PROVIDER)
        references, relay, relay_digest = {}, None, None
        if phase == "submit" and not inherited:
            try:
                specs, _ = inspect_references(json.loads(job["input_json"])["request"])
                references = freeze_image_references(specs, resources)
            except (ActionError, OSError, ValueError):
                raise MediaExecutionRejected("action_execution_reference_changed") from None
            if references:
                relay = get_live_reference_relay_config()
                relay_digest = connection_binding("reference_relay", relay=relay)

        def validate():
            with closing(db_connect()) as conn:
                validate_video_ownership(conn, job, receipt)
            observed = connection_binding(PROVIDER)
            if not builtin_provider_enabled(PROVIDER) or not hmac.compare_digest(observed, receipt["connection_digest"]):
                raise MediaExecutionRejected("pollinations_video_connection_changed")
            if relay_digest and not hmac.compare_digest(connection_binding("reference_relay"), relay_digest):
                raise MediaExecutionRejected("action_execution_connection_changed")

        snapshot_digest = connection_binding(PROVIDER, runtime=runtime, transport=transport)
        if not hmac.compare_digest(snapshot_digest, receipt["connection_digest"]):
            raise MediaExecutionRejected("pollinations_video_connection_changed")
        if inherited:
            with inherited.validation_guard(validate):
                inherited.require_target(*TARGET, operation=phase)
                yield inherited
        else:
            snapshot = MediaExecutionSnapshot(
                targets=[TARGET], runtimes={PROVIDER: runtime}, transports={PROVIDER: transport},
                custom={}, references=references, relay=relay, validate=validate, operation=phase,
            )
            with media_execution_scope(snapshot):
                snapshot.require_target(*TARGET, operation=phase)
                yield snapshot
