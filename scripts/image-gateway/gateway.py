"""兼容入口：导出 AngeMedia Gateway 模块化后的 FastAPI app。

旧路径 scripts/image-gateway/gateway.py 保留，方便现有测试、脚本和用户命令继续工作。
新的实现位于 scripts/angemedia_gateway/。
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from angemedia_gateway.server import app, create_image, create_video, get_video, require_auth  # noqa: E402
from angemedia_gateway.adapters.agnes_video import AgnesVideoProvider, VideoRequest  # noqa: E402
from angemedia_gateway.providers.image import normalize_image_response, parse_size  # noqa: E402
from angemedia_gateway.providers.base import BackendUnavailable, RateLimited, RouteTarget  # noqa: E402
from angemedia_gateway.routing import MODEL_ALIASES, DEFAULT_CHAIN, resolve_chain  # noqa: E402

if __name__ == "__main__":
    import uvicorn
    from angemedia_gateway import config as C
    uvicorn.run(app, host=C.HOST, port=C.PORT)
