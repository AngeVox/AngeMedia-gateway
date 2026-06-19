"""Image provider registry."""
from __future__ import annotations

from ..base import ProviderBase
from ..mock import MockImageProvider
from .agnes import AgnesImageProvider
from .bytedance import ByteDanceImageProvider
from .modelscope import ModelScopeProvider
from .openai_compatible import OpenAICompatibleImageProvider
from .pollinations import PollinationsProvider
from .siliconflow import SiliconFlowProvider


def build_providers() -> dict[str, ProviderBase]:
    return {
        "siliconflow": SiliconFlowProvider(),
        "modelscope": ModelScopeProvider(),
        "pollinations": PollinationsProvider(),
        "openai_image": OpenAICompatibleImageProvider(),
        "agnes_image": AgnesImageProvider(),
        "bytedance": ByteDanceImageProvider(),
        "mock": MockImageProvider(),
    }
