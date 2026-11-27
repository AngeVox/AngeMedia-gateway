"""公开视频入口与持久队列共用参考图交付规则，不上传图片或生成中转链接。"""
from ..reference_images import (materialize_gateway_image_reference,
                                validate_gateway_image_reference)
from ..providers.reference_delivery import PUBLIC_URL, RELAY_REQUIRED, prepare_builtin_reference
from ..providers.reference_relay import configured_reference_relay_backend
from ..video_models import is_agnes_video_modern


class InvalidVideoReference(ValueError):
    """参考图不符合当前视频型号的既有交付合同。"""


def validate_video_reference_sources(req, *, materialize=False):
    """入队需确认本地材料可读；旧即时入口保留原验证时机和公开URL能力。"""
    references = ([req.image] if req.image else []) + list(req.images or [])
    modern = is_agnes_video_modern(req.model)
    if modern:
        references.extend(item for item in (req.first_frame, req.last_frame) if item)
    for reference in references:
        try:
            if modern:
                decision = prepare_builtin_reference('agnes_video', reference, model=req.model)
                if decision.kind == PUBLIC_URL:
                    continue
                if decision.kind != RELAY_REQUIRED:
                    raise InvalidVideoReference('Agnes Video 2.5 reference image delivery is not supported')
                if decision.source == 'gateway_asset':
                    validate_gateway_image_reference(reference)
                elif materialize:
                    # 即时API仍可处理安全data URL；持久队列不把二进制图片写入任务正文。
                    validate_gateway_image_reference(reference)
                if configured_reference_relay_backend() is None:
                    raise InvalidVideoReference('Agnes Video 2.5 local reference requires a configured reference relay')
            else:
                validate_gateway_image_reference(reference)
            if materialize:
                materialize_gateway_image_reference(reference)
        except InvalidVideoReference:
            raise
        except ValueError:
            message = ('Agnes Video 2.5 reference image is not supported' if modern
                       else 'reference image must be an uploaded or generated image asset')
            raise InvalidVideoReference(message) from None
