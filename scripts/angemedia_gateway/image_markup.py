"""校验 OpenAI 标注编辑的原图与透明图层。"""
from __future__ import annotations

from io import BytesIO

from PIL import Image


_MAX_PIXELS = 16_777_216
_MESSAGES = {
    "source_required": "Choose the original image for the annotation.",
    "source_unreadable": "The original must be a readable PNG, JPEG or WebP image.",
    "overlay_unreadable": "The annotation must be a readable PNG image stored in AngeMedia.",
    "animated": "Use still images for the original and annotation.",
    "size_limit": "The original and annotation can each contain at most 16,777,216 pixels.",
    "size_mismatch": "The annotation and original must have the same dimensions after orientation.",
    "opaque": "The annotation PNG must have transparent pixels.",
}


class ImageMarkupError(ValueError):
    def __init__(self, reason: str) -> None:
        self.code = f"image_markup_{reason}"
        super().__init__(_MESSAGES[reason])


def _inspect(content: bytes, *, overlay: bool) -> tuple[int, int]:
    role = "overlay" if overlay else "source"
    formats = ("PNG",) if overlay else ("PNG", "JPEG", "WEBP")
    try:
        with Image.open(BytesIO(content), formats=formats) as image:
            width, height = image.size
            if width * height > _MAX_PIXELS:
                raise ImageMarkupError("size_limit")
            if getattr(image, "is_animated", False) or image.get_format_mimetype() == "image/apng":
                raise ImageMarkupError("animated")
            # EXIF 5 到 8 交换坐标轴。只核对显示尺寸，不旋转或重编码要发出的文件。
            oriented_size = (height, width) if image.getexif().get(274) in {5, 6, 7, 8} else (width, height)
            image.load()
            if overlay and (not image.has_transparency_data or image.convert("RGBA").getchannel("A").getextrema()[0] == 255):
                raise ImageMarkupError("opaque")
            return oriented_size
    except ImageMarkupError:
        raise
    except Image.DecompressionBombError as exc:
        raise ImageMarkupError("size_limit") from exc
    except (OSError, ValueError, SyntaxError, TypeError, OverflowError) as exc:
        raise ImageMarkupError(f"{role}_unreadable") from exc


def validate_image_markup(source: bytes, overlay: bytes) -> None:
    if _inspect(source, overlay=False) != _inspect(overlay, overlay=True):
        raise ImageMarkupError("size_mismatch")
