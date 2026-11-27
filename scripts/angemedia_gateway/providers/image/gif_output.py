"""按 GIF 动画合同解码有界帧，保留原始字节交给既有作品流程。"""
from __future__ import annotations

from io import BytesIO

from PIL import Image

from ...schemas import ImageRequest
from ..errors import ProviderProtocolError

# 输出画布上限复用现有 2.6 模型的尺寸安全边界。
_MAX_SIDE = 3840
_MAX_PIXELS = 8_294_400


def inspect_gif_output(content: bytes, request: ImageRequest) -> dict[str, int]:
    width = int.from_bytes(content[6:8], "little")
    height = int.from_bytes(content[8:10], "little")
    canvas = (width, height)
    if (not content.endswith(b";") or not (0 < width <= _MAX_SIDE and 0 < height <= _MAX_SIDE)
        or width * height > _MAX_PIXELS):
        raise ProviderProtocolError("Image provider returned an invalid GIF canvas")
    if request.size != "auto" and request.size != f"{width}x{height}":
        raise ProviderProtocolError("Image provider returned different GIF frame dimensions")

    expected_count = request.frame_count if request.frame_count is not None else 9
    expected_delay = request.frame_duration_ms if request.frame_duration_ms is not None else 200
    try:
        with Image.open(BytesIO(content), formats=["GIF"]) as animation:
            delays = []
            # 逐帧解码到预期数量，避免先遍历上游声明的所有帧或持有全部像素。
            for index in range(expected_count):
                animation.seek(index)
                if animation.size != canvas:
                    raise ProviderProtocolError("Image provider returned a frame outside the GIF canvas")
                animation.load()
                delay = animation.info.get("duration", 0)
                if not 20 <= delay <= 2000 or abs(delay - expected_delay) > 5:
                    raise ProviderProtocolError("Image provider returned an unexpected GIF frame delay")
                delays.append(delay)
            try:
                animation.seek(expected_count)
            except EOFError:
                return {"frame_count": expected_count, "animation_duration_ms": sum(delays)}
            raise ProviderProtocolError("Image provider returned more GIF frames than requested")
    except (EOFError, OSError, ValueError, Image.DecompressionBombError):
        raise ProviderProtocolError("Image provider returned an incomplete or invalid GIF") from None
