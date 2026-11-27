"""图层成员的存储命名空间，供写入、下载和删除入口共用。"""
from pathlib import Path


LAYER_NAMESPACE = ".image-layers"


def is_layer_relative_path(path: str) -> bool:
    parts = Path(path).parts
    return bool(parts) and parts[0].lower() == LAYER_NAMESPACE
