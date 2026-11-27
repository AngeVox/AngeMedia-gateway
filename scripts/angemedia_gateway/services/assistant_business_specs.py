"""业务工具的公开参数、能力与副作用声明；不依赖SDK或执行上下文。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyArgs(StrictArgs):
    pass


class JobArgs(StrictArgs):
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class KnowledgeArgs(StrictArgs):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=3, ge=1, le=4)


class ProviderArgs(StrictArgs):
    provider_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")


class ChannelArgs(StrictArgs):
    provider_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    limit: int = Field(default=5, ge=1, le=10)
    offset: int = Field(default=0, ge=0, le=10000)


class ModelArgs(StrictArgs):
    model: str = Field(min_length=1, max_length=160)


class LogsArgs(StrictArgs):
    source: Literal["all", "api", "dispatcher", "worker"] = "all"
    line_limit: int = Field(default=5, ge=1, le=8)


class AssetArgs(StrictArgs):
    asset_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    job_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    media_type: Literal["image", "video"] | None = None
    limit: int = Field(default=5, ge=1, le=10)
    offset: int = Field(default=0, ge=0, le=10000)


class JobSearchArgs(StrictArgs):
    kind: Literal['image', 'video'] | None = None
    status: Literal['queued', 'running', 'succeeded', 'failed', 'canceled'] | None = None
    limit: int = Field(default=5, ge=1, le=10)
    offset: int = Field(default=0, ge=0, le=10000)


@dataclass(frozen=True)
class BusinessToolSpec:
    name: str
    schema: type[BaseModel]
    description: str
    effects: tuple[str, ...]


BUSINESS_TOOLS = (
    BusinessToolSpec('job_search', JobSearchArgs, '按状态或图像/视频类型分页检索最近任务，适合没有任务ID时先找失败任务；拿到ID后查询详情和关联作品。', ('database_read',)),
    BusinessToolSpec("local_knowledge_base", KnowledgeArgs, "搜索本地AngeMedia产品知识，适合操作说明和创作流程。", ("bundled_files_read",)),
    BusinessToolSpec("job_safe_summary", JobArgs, "按真实任务ID读取状态、阶段、最近尝试与关联作品地址；不存在会失败。", ("database_read",)),
    BusinessToolSpec("failure_diagnostic", JobArgs, "读取指定任务的失败分类、处理建议及可重试性，不执行重试。", ("database_read",)),
    BusinessToolSpec("diagnostics_summary", EmptyArgs, "读取系统安全诊断与受控队列健康检查，不扫描媒体目录、不修改配置。", ("database_read", "broker_probe", "controlled_path_stat")),
    BusinessToolSpec("queue_status", EmptyArgs, "读取任务队列和投递状态，检查已配置broker的连通性，不提交或撤销任务。", ("database_read", "broker_probe")),
    BusinessToolSpec("recent_logs", LogsArgs, "读取固定AngeMedia日志来源的有界脱敏尾部，不能指定路径。", ("bounded_log_read",)),
    BusinessToolSpec("channel_safe_summary", ChannelArgs, "分页读取渠道配置与已有连接证据；密钥只返回是否配置，不表示生成已验证。", ("database_read",)),
    BusinessToolSpec("catalog_model_capabilities", ModelArgs, "读取指定模型的目录声明能力和引用图约束；目录声明不等于当前账户实测权限。", ("bundled_files_read",)),
    BusinessToolSpec("asset_search", AssetArgs, "按作品ID、任务ID或媒体类型检索真实作品；只返回安全元数据和受控下载路径。", ("database_read",)),
    BusinessToolSpec("provider_connection_test", ProviderArgs, "对已配置内置渠道请求非生成模型列表，会发送渠道凭据；不生成媒体、不写连接证据。", ("database_read", "provider_model_list_request")),
    BusinessToolSpec("network_probe", ProviderArgs, "对已配置内置渠道做受控DNS/TCP/TLS诊断，不接受URL或凭据，不生成媒体。", ("database_read", "provider_dns_tcp_tls_probe")),
)
