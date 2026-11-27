"""复用媒体准备服务生成动作候选；慢读取在写事务之外，提交时只复核观察。"""
from __future__ import annotations

from contextlib import closing

from pydantic import ValidationError

from ..assistant_action_contract import ActionCandidate, ActionError
from ..db.connection import db_connect
from ..db.readonly_scope import readonly_database
from ..job_sanitizer import sanitize_error_text, sanitize_job_value
from ..layered_images import LayeredImageRequest
from ..schemas import ImageRequest, VideoRequest
from ..pollinations_video import PollinationsVideoRequest
from ..video_models import is_agnes_video_modern
from ..repositories.settings import get_custom_provider
from .assistant_action_environment import environment_stamp
from .assistant_action_policy import action_policy
from .assistant_action_references import inspect_references, references_unchanged
from .assistant_business_secrets import configured_secrets, redact_values
from .image_job_admission import ImageJobAdmissionService
from .layer_image_admission import prepare_layer_job
from .video_job_admission import VideoJobAdmissionService
from .image_execution import CustomProviderNotFound, InvalidImageRequest, NoImageProviderAvailable
from .video_execution import VideoProviderDisabled


class ActionPreparer:
    def __init__(self, db_path):
        self.db_path = db_path

    def stamp(self, conn):
        return environment_stamp(conn)

    def unchanged(self, conn, candidate):
        return self.stamp(conn) == candidate.environment_stamp and references_unchanged(candidate.checks())

    def prepare(self, kind, request):
        if kind not in {'image', 'video'} or not isinstance(request, dict):
            raise ActionError('invalid_action_request')
        with readonly_database(self.db_path):
            with closing(db_connect()) as conn: before = self.stamp(conn)
            try:
                if kind == 'image' and request.get('operation') == 'decompose':
                    prepared = prepare_layer_job(LayeredImageRequest.model_validate(request))
                elif kind == 'image':
                    model = ImageRequest.model_validate(request)
                    if model.model and model.model.startswith('custom:') and not model.provider_model:
                        provider = get_custom_provider(model.model.split(':', 1)[1], include_secret=False)
                        if provider is None:
                            raise ActionError('action_provider_not_found')
                        selected = str((provider or {}).get('default_model') or '').strip()
                        if not selected: raise ActionError('model_selection_required')
                        model.provider_model = selected
                    prepared = ImageJobAdmissionService().prepare(model)
                else:
                    request_type = PollinationsVideoRequest if request.get('provider') == 'pollinations' else VideoRequest
                    model = request_type.model_validate(request)
                    # 旧协议的默认24与显式24.0须形成同一动作快照，避免无修改确认被误判为漂移。
                    if isinstance(model, VideoRequest) and not is_agnes_video_modern(model.model):
                        model.frame_rate = float(model.frame_rate)
                    prepared = VideoJobAdmissionService().prepare(model)
                arguments = prepared.arguments(); effective = arguments['payload']['request']
                # 拒绝有损投影，不能让确认内容与worker实际读取的正文、引用不一致。
                if sanitize_job_value(arguments['payload']) != arguments['payload'] or sanitize_error_text(arguments['prompt'], limit=8000) != arguments['prompt']:
                    raise ActionError('action_payload_not_preservable')
                references, checks = inspect_references(effective)
                policy, bindings = action_policy(arguments, references)
                body = {'kind': kind, 'request': effective, 'admission': arguments,
                        'review': {'kind': kind, 'request': effective, 'references': references, **policy},
                        'configuration_bindings': bindings}
                if redact_values(body, configured_secrets()) != body:
                    raise ActionError('action_contains_sensitive_value')
            except ActionError:
                raise
            except (NoImageProviderAvailable, VideoProviderDisabled):
                raise ActionError('action_provider_unavailable') from None
            except CustomProviderNotFound:
                raise ActionError('action_provider_not_found') from None
            except (ValidationError, InvalidImageRequest, ValueError):
                raise ActionError('invalid_action_request') from None
            except Exception:
                # 现有校验可能包含用户正文或路径，动作域仅传递固定分类。
                raise ActionError('action_preparation_failed') from None
            with closing(db_connect()) as conn:
                if before != self.stamp(conn) or not references_unchanged(checks):
                    raise ActionError('action_environment_changed')
            return ActionCandidate.build(body, before, checks)
