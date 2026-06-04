"""兼容入口：Agnes 视频适配器已迁移到 scripts/angemedia_gateway/adapters/agnes_video.py。"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from angemedia_gateway.adapters.agnes_video import AgnesVideoError, AgnesVideoProvider, VideoRequest  # noqa: E402,F401
