"""原生提案工具的公开参数；无用户名、Cookie、运行上下文或确认字段。"""
from pydantic import Field, JsonValue

from ..schemas import ImageRequest
from .video_job_admission import QueuedVideoRequest
from ..layered_images import LayeredImageRequest
from .assistant_business_specs import BusinessToolSpec, StrictArgs


class ImageProposalArgs(StrictArgs):
    request: LayeredImageRequest | ImageRequest


class VideoProposalArgs(StrictArgs):
    request: QueuedVideoRequest


class ProposalRevisionArgs(StrictArgs):
    action_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    version: int = Field(ge=1)
    changes: dict[str, JsonValue] = Field(min_length=1, max_length=48)


class ProposalListArgs(StrictArgs):
    limit: int = Field(default=3, ge=1, le=5)


ACTION_TOOLS = (
    BusinessToolSpec('propose_image', ImageProposalArgs, '根据明确的出图需求创建图片提案，展示到确认卡；不会生成或收费。普通图片传入完整请求。用户明确要求动态 GIF 时可用 GPT Image 2.6 的 gif 输出，传入帧数和每帧时长可选值，编辑使用普通参考图。用户明确要求 SVG 时可用 GPT Image 2.6 文字生图，output_format 为 svg，不带参考图或编辑参数。用户提供分开的原图和透明 PNG 标注并要求按标注编辑时，用 GPT Image 2.6 静态输出，原图放在 image，标注放在 markup，辅助参考图放在 reference_images，不带 mask。只引用用户已提供的网关图片，不自行编造路径或把标注当原图。用户明确要求拆分图层时，用 seedream-5-5 和 decompose，提供一张网关作品或上传图片以及包含底图的最大结果数量。只有用户明确要另一份才新建提案。', ('proposal_write',)),
    BusinessToolSpec('propose_video', VideoProposalArgs, '根据明确的视频需求创建视频提案，展示到确认卡；不会提交视频。传入符合型号协议的视频请求，不等待生成完成；远程引用可能变化。Wan 2.2 Fast 使用 provider=pollinations 和 model=alibaba/wan-2.2-fast。', ('proposal_write',)),
    BusinessToolSpec('revise_media_proposal', ProposalRevisionArgs, '修改本会话尚未确认提案。先读取media_action_list取得真实id和当前version，只传要改的请求字段；服务端保留其余完整内容。旧确认失效，仍需用户在卡片确认。', ('proposal_write',)),
    BusinessToolSpec('media_action_list', ProposalListArgs, '读取本会话最近提案、当前版本和真实任务引用；提示词仅摘要，修改时只传变化字段，不用摘要替换全文。', ('database_read',)),
)
