"""分层任务提交与只取回恢复，复用任务版本、投递和作品事务。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from uuid import UUID, uuid5

from .. import config as C
from ..db.connection import db_transaction
from ..layered_images import LAYER_MODEL
from ..media_execution_scope import MediaExecutionRejected
from ..providers.errors import ProviderProtocolError
from ..providers.image.bytedance import ByteDanceImageProvider
from ..queue.contracts import QueueDispatchEnvelope
from ..queue.settings import WORKER_TASK_NAME
from ..repositories.assets import save_asset
from ..repositories.assistant_action_execution import job_intent_digest, load_grant
from ..repositories.generations import record_generation
from ..repositories.image_layers import (
    accept_layer_receipt, close_layer_receipt, layer_receipt, prepare_layer_receipt,
)
from ..repositories.job_attempts import create_job_attempt, finish_job_attempt, get_job_attempt
from ..repositories.job_dispatches import create_job_dispatch
from ..repositories.jobs import claim_job_attempt, get_job, transition_job_in_connection
from .assistant_action_execution import action_job_execution
from .generation_assets import safe_output_json
from .layer_image_admission import layer_job_request
from .layer_image_files import (
    LayerArtifactRejected, LayerDownloadExpired, discard_layer_files, fetch_layer_files,
)


class LayerLeaseLost(RuntimeError):
    pass


class LayerImageWorker:
    def __init__(self, *, worker_kind="celery", provider=None, clock=None):
        self.worker_kind = worker_kind
        self.provider = provider or ByteDanceImageProvider()
        self.now = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _result(message, status):
        return {"job_id": message.job_id, "status": status, "stage": message.stage,
                "attempt": message.attempt, "dispatch_id": message.dispatch_id}

    @staticmethod
    def _dispatch(conn, message, stage, attempt, when):
        envelope = QueueDispatchEnvelope(message.job_id, "image", stage, 1, attempt)
        create_job_dispatch(job_id=message.job_id, topic=WORKER_TASK_NAME, payload=envelope.as_dict(),
                            available_at=when.isoformat(), conn=conn)

    def _finish(self, conn, message, status, *, code=None, retry_at=None):
        finish_job_attempt(job_id=message.job_id, attempt_number=message.attempt, status=status,
                           completed_at=self.now().isoformat(), error_code=code, retry_at=retry_at, conn=conn)

    def _transition(self, conn, job, status, **fields):
        return transition_job_in_connection(conn, job["id"], expected_version=job["version"], status=status, **fields)

    def _fail(self, conn, message, job, code, category):
        close_layer_receipt(conn, job["id"])
        self._transition(conn, job, "failed", error_code=code, error_category=category, error_message="",
                         completed_at=self.now().isoformat(), retryable=False, next_retry_at="", claim_expires_at="")
        self._finish(conn, message, "failed", code=code)
        return self._result(message, "failed")

    @staticmethod
    def _owned(conn, expected):
        job = get_job(expected["id"], conn=conn)
        if (job is None or job["version"] != expected["version"] or job["status"] != "running"
                or job["stage"] != expected["stage"]):
            raise LayerLeaseLost()
        return job

    @staticmethod
    def _authorized(conn, job, receipt=None):
        if load_grant(conn, job) is None:
            raise MediaExecutionRejected("action_execution_grant_missing")
        if receipt is not None and job_intent_digest(job) != receipt["intent_digest"]:
            raise MediaExecutionRejected("action_execution_payload_changed")

    def _claim(self, conn, message, job):
        claimed = claim_job_attempt(conn, job_id=job["id"], expected_version=job["version"], stage=message.stage,
                                    attempt_count=message.attempt, worker_kind=self.worker_kind, provider="bytedance",
                                    model=LAYER_MODEL, started_at=self.now().isoformat(), allow_running=True)
        if claimed is None:
            raise LayerLeaseLost()
        return claimed

    def _start_attempt(self, conn, message):
        create_job_attempt(job_id=message.job_id, attempt_number=message.attempt, stage=message.stage,
                           worker_kind=self.worker_kind, status="running", started_at=self.now().isoformat(),
                           detail={"dispatch_id": message.dispatch_id}, conn=conn)

    def _begin_import(self, conn, message, job):
        now = self.now()
        self._transition(conn, job, "running", stage="asset_import", provider_status="received",
                         next_retry_at=now.isoformat(), claim_expires_at="")
        self._finish(conn, message, "succeeded")
        self._dispatch(conn, message, "asset_import", message.attempt + 1, now)

    def _claim_submit(self, message, job):
        with db_transaction(immediate=True) as conn:
            current = get_job(job["id"], conn=conn)
            if current is None or current["stage"] != message.stage or current["status"] not in {"queued", "running"}:
                raise LayerLeaseLost()
            receipt = layer_receipt(conn, current["id"])
            if receipt is not None:
                if current["attempt_count"] != message.attempt:
                    return None, self._result(message, "stale")
                try:
                    self._authorized(conn, current, receipt)
                except MediaExecutionRejected as exc:
                    return None, self._fail(conn, message, current, exc.code, "action_authorization")
                if receipt["state"] == "received":
                    self._begin_import(conn, message, current)
                    return None, self._result(message, "scheduled")
                if self.now() < datetime.fromisoformat(receipt["deadline"]):
                    return None, self._result(message, "inflight")
                return None, self._fail(conn, message, current, "image_layer_submit_unknown", "ambiguous_submit")
            if current["status"] != "queued" or message.attempt != 1:
                raise LayerLeaseLost()
            claimed = self._claim(conn, message, current)
            self._start_attempt(conn, message)
            try:
                self._authorized(conn, claimed)
            except MediaExecutionRejected as exc:
                return None, self._fail(conn, message, claimed, exc.code, "action_authorization")
            deadline = self.now() + timedelta(seconds=max(60, float(C.HTTP_TIMEOUT) * 4))
            prepare_layer_receipt(conn, job["id"], job_intent_digest(claimed), deadline.isoformat())
            self._dispatch(conn, message, message.stage, message.attempt, deadline)
            return claimed, None

    def handle_generate(self, message, job):
        try:
            claimed, result = self._claim_submit(message, job)
            if result is not None:
                return result
            try:
                with action_job_execution(claimed):
                    entries, expires = asyncio.run(self.provider.decompose(layer_job_request(claimed)))
            except Exception as exc:
                if isinstance(exc, MediaExecutionRejected):
                    code, category = exc.code, "action_authorization"
                elif isinstance(exc, ProviderProtocolError):
                    code, category = "image_layer_result_invalid", "provider_response"
                else:
                    code, category = "image_layer_submit_unknown", "ambiguous_submit"
                with db_transaction(immediate=True) as conn:
                    return self._fail(conn, message, self._owned(conn, claimed), code, category)
            try:
                with db_transaction(immediate=True) as conn:
                    current = self._owned(conn, claimed)
                    self._authorized(conn, current, layer_receipt(conn, job["id"]))
                    accept_layer_receipt(conn, job["id"], entries, expires.isoformat())
                    self._begin_import(conn, message, current)
            except sqlite3.Error:
                # 投递写入失败时保留收据，由原提交守护接续取回。
                with db_transaction(immediate=True) as conn:
                    current = self._owned(conn, claimed)
                    self._authorized(conn, current, layer_receipt(conn, job["id"]))
                    accept_layer_receipt(conn, job["id"], entries, expires.isoformat())
                return self._result(message, "receipt_saved")
            return self._result(message, "scheduled")
        except MediaExecutionRejected as exc:
            with db_transaction(immediate=True) as conn:
                try:
                    current = self._owned(conn, claimed)
                except LayerLeaseLost:
                    return self._result(message, "stale")
                return self._fail(conn, message, current, exc.code, "action_authorization")
        except LayerLeaseLost:
            return self._result(message, "stale")

    def _claim_import(self, message, job):
        with db_transaction(immediate=True) as conn:
            current = get_job(job["id"], conn=conn)
            if current is None or current["stage"] != message.stage or current["status"] != "running":
                raise LayerLeaseLost()
            now = self.now()
            attempt = get_job_attempt(job["id"], message.attempt, conn=conn)
            if attempt:
                if attempt["status"] != "running" or message.attempt != current["attempt_count"]:
                    raise LayerLeaseLost()
                if now < datetime.fromisoformat(current["claim_expires_at"]):
                    return None, None, self._result(message, "inflight")
            elif (message.attempt != current["attempt_count"] + 1
                  or now < datetime.fromisoformat(current["next_retry_at"])):
                return None, None, self._result(message, "not_due")
            claimed = self._claim(conn, message, current)
            if attempt is None:
                self._start_attempt(conn, message)
            receipt = layer_receipt(conn, job["id"])
            if receipt is None or receipt["state"] != "received":
                return None, None, self._fail(conn, message, claimed, "image_layer_receipt_missing", "provider_response")
            try:
                self._authorized(conn, claimed, receipt)
            except MediaExecutionRejected as exc:
                return None, None, self._fail(conn, message, claimed, exc.code, "action_authorization")
            lease = now + timedelta(minutes=3)
            claimed = self._transition(conn, claimed, "running", claim_expires_at=lease.isoformat())
            self._dispatch(conn, message, message.stage, message.attempt, lease)
            return claimed, receipt, None

    def _publish(self, message, claimed, files):
        with db_transaction(immediate=True) as conn:
            job = self._owned(conn, claimed)
            self._authorized(conn, job, layer_receipt(conn, job["id"]))
            request = layer_job_request(job)
            members = []
            duration = max(0, int((self.now() - datetime.fromisoformat(job["started_at"])).total_seconds() * 1000))
            for item in files:
                relative = item["relative_path"]
                asset_id = uuid5(UUID(job["id"]), f'layer:{item["z_index"]}').hex
                url = f"/generated/{relative}"
                save_asset(id=asset_id, filename=relative.rsplit("/", 1)[-1], storage_area="output",
                           relative_path=relative, url_path=url, media_type="image", source="generated",
                           size=item["bytes"], prompt=request.prompt, model=LAYER_MODEL, provider="bytedance",
                           duration_ms=duration, job_id=job["id"], conn=conn)
                public = {key: value for key, value in item.items() if key != "relative_path"}
                members.append({**public, "asset_id": asset_id, "url": url})
            result = {"data": [{"url": item["url"]} for item in members], "provider": "bytedance",
                      "model": LAYER_MODEL, "duration_ms": duration}
            result["history_id"] = record_generation(
                "image", request.prompt, None, LAYER_MODEL, "completed", result, provider="bytedance",
                request_model=request.model, input_mode="image_layer_decomposition", duration_ms=duration,
                started_at=job["started_at"], job_id=job["id"], conn=conn,
            )
            close_layer_receipt(conn, job["id"], manifest={"version": 1, "job_id": job["id"], "members": members})
            self._transition(conn, job, "succeeded", provider_status="completed",
                             output_json=safe_output_json(result), duration_ms=duration,
                             completed_at=self.now().isoformat(), retryable=False, next_retry_at="", claim_expires_at="",
                             error_code="", error_category="")
            self._finish(conn, message, "succeeded")

    def _retry_import(self, conn, message, job):
        if message.attempt >= job["max_attempts"]:
            return self._fail(conn, message, job, "image_layer_import_failed", "provider_response")
        when = self.now() + timedelta(seconds=min(60, 5 * 2 ** (message.attempt - 2)))
        self._transition(conn, job, "running", next_retry_at=when.isoformat(), claim_expires_at="",
                         error_code="image_layer_import_retry", retryable=True, gateway_stage="asset_import")
        self._finish(conn, message, "failed", code="image_layer_import_retry", retry_at=when.isoformat())
        self._dispatch(conn, message, message.stage, message.attempt + 1, when)
        return self._result(message, "scheduled")

    def handle_import(self, message, job):
        result = self._import_layers(message, job)
        if result["status"] == "failed":
            discard_layer_files(message.job_id)
        return result

    def _import_layers(self, message, job):
        try:
            claimed, receipt, result = self._claim_import(message, job)
            if result is not None:
                return result

            def commit_member(publish_file):
                with db_transaction(immediate=True) as conn:
                    self._owned(conn, claimed)
                    publish_file()

            try:
                files = asyncio.run(fetch_layer_files(job["id"], json.loads(receipt["result_json"]),
                                                      datetime.fromisoformat(receipt["deadline"]), self.now, commit_member))
                self._publish(message, claimed, files)
            except LayerLeaseLost:
                raise
            except Exception as exc:
                with db_transaction(immediate=True) as conn:
                    current = self._owned(conn, claimed)
                    if isinstance(exc, LayerDownloadExpired):
                        return self._fail(conn, message, current, "image_layer_links_expired", "local_timeout")
                    if isinstance(exc, LayerArtifactRejected):
                        return self._fail(conn, message, current, "image_layer_artifact_invalid", "provider_response")
                    if isinstance(exc, MediaExecutionRejected):
                        return self._fail(conn, message, current, exc.code, "action_authorization")
                    return self._retry_import(conn, message, current)
            return self._result(message, "succeeded")
        except LayerLeaseLost:
            return self._result(message, "stale")
