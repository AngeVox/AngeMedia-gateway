"""分层提案复用已确认图片入队，持久载荷显式区分分层和单图。"""
import json

from ..job_submission import PreparedJobAdmission, submission_intent_hash
from ..layered_images import LAYER_MODEL, LayeredImageRequest, layer_reference
from .image_execution import ImageExecutionPlan

LAYER_PLAN = ImageExecutionPlan(mode="builtin", routes=(("bytedance", LAYER_MODEL),))
LAYER_PURPOSE = "image_layer_decomposition"


def is_layer_image_job(job):
    if job.get("kind") != "image" or job.get("model") != LAYER_MODEL:
        return False
    try:
        payload = json.loads(job["input_json"])
        return isinstance(payload, dict) and payload.get("operation_kind") == LAYER_PURPOSE
    except (KeyError, ValueError, TypeError):
        return False


def prepare_layer_job(request: LayeredImageRequest) -> PreparedJobAdmission:
    if not request.prompt.strip():
        raise ValueError("分层提示词不能为空")
    layer_reference(request)
    payload = {"schema_version": 1, "operation_kind": LAYER_PURPOSE,
               "request": request.model_dump(), "route": LAYER_PLAN.to_dict()}
    return PreparedJobAdmission.from_arguments(
        kind="image", stage="image_generate", payload=payload,
        model=LAYER_MODEL, provider="bytedance", prompt=request.prompt,
        request_hash=submission_intent_hash(payload), request_hash_version=1, max_attempts=6,
    )


def layer_job_request(job) -> LayeredImageRequest:
    payload = json.loads(job["input_json"])
    if (payload["schema_version"] != 1 or payload["operation_kind"] != LAYER_PURPOSE
            or payload["route"] != LAYER_PLAN.to_dict()):
        raise ValueError("分层任务快照无效")
    return LayeredImageRequest.model_validate(payload["request"])
