"""Seedream 分层请求与返回值的专用合同。"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .job_sanitizer import sanitize_error_text
from .job_submission import canonical_submission_json
from .providers.errors import ProviderProtocolError
from .reference_images import materialize_gateway_image_reference
from .security import validate_provider_reference_url


LAYER_MODEL = "dola-seedream-5-5-261028"


class LayeredImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: Literal["decompose"]
    model: Literal["seedream-5-5"]
    image: str = Field(min_length=1, max_length=2048)
    prompt: str = Field(min_length=1, max_length=8000)
    max_images: int = Field(ge=2, le=100)
    size: Literal["auto", "1K", "1.5K", "2K"] = "auto"
    output_format: Literal["png", "jpeg"] = "png"
    text_recognition: bool = False
    watermark: bool = True


def image_geometry(data: bytes) -> tuple[str, int, int]:
    """读取 PNG 或 JPEG 文件头中的尺寸。"""
    if (len(data) >= 45 and data[:16] == b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            and data[-8:-4] == b"IEND"):
        width, height = int.from_bytes(data[16:20]), int.from_bytes(data[20:24])
        return "png", width, height
    if data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9":
        offset = 2
        while offset < len(data) - 8 and data[offset] == 255:
            while offset < len(data) - 8 and data[offset] == 255:
                offset += 1
            marker = data[offset]
            length = int.from_bytes(data[offset + 1:offset + 3])
            if length < 2 or offset + 1 + length > len(data):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and length >= 8:
                height = int.from_bytes(data[offset + 4:offset + 6])
                width = int.from_bytes(data[offset + 6:offset + 8])
                return "jpeg", width, height
            if marker in {0xD9, 0xDA}:
                break
            offset += 1 + length
    raise ValueError("分层只支持有效的 PNG 或 JPEG 图片")


def layer_reference(request: LayeredImageRequest) -> str:
    value = materialize_gateway_image_reference(request.image)
    _, width, height = image_geometry(base64.b64decode(value.partition(",")[2], validate=True))
    if not 262144 <= width * height <= 36000000 or max(width, height) > 16 * min(width, height):
        raise ValueError("分层参考图的像素总数或宽高比超限")
    return value


def _text(value, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError()
    return sanitize_error_text(value, limit=limit)


def _coordinates(value, width, height):
    if not isinstance(value, dict):
        raise ValueError()
    boxes = {}
    for key, w, h in (("absolute", width, height), ("normalized", 1000, 1000)):
        rectangle = value.get(key)
        if (not isinstance(rectangle, list) or len(rectangle) != 4
                or any(type(number) is not int for number in rectangle)):
            raise ValueError()
        left, top, right, bottom = rectangle
        if not (0 <= left <= right <= w and 0 <= top <= bottom <= h):
            raise ValueError()
        boxes[key] = rectangle
    return boxes


def _ocr(value, width, height):
    if not isinstance(value, dict) or not isinstance(value.get("lines"), list) or len(value["lines"]) > 256:
        raise ValueError()
    lines = []
    for line in value["lines"]:
        raw_runs = line["runs"]
        if not isinstance(raw_runs, list) or len(raw_runs) > 256:
            raise ValueError()
        runs = []
        for raw_run in raw_runs:
            run = {"text": _text(raw_run["text"], 4096)}
            if "style" in raw_run:
                raw_style = raw_run["style"]
                if not isinstance(raw_style, dict):
                    raise ValueError()
                style = {key: _text(raw_style[key], 256) for key in ("similar_font_family", "foreground_color") if key in raw_style}
                for key in ("bold", "italic", "underline", "strikethrough", "shadow", "translucent"):
                    if key in raw_style:
                        if type(raw_style[key]) is not bool:
                            raise ValueError()
                        style[key] = raw_style[key]
                run["style"] = style
            runs.append(run)
        lines.append({"bounding_box": _coordinates(line["bounding_box"], width, height), "runs": runs})
    return {"text": _text(value["text"], 32768), "lines": lines}


def parse_layer_result(response: dict, request: LayeredImageRequest, received_at: datetime) -> tuple[list[dict], datetime]:
    """验证整组后才返回取回收据，签名链接只留在内部收据。"""
    try:
        items = response["data"]
        if not isinstance(items, list) or not 2 <= len(items) <= request.max_images:
            raise ValueError()
        order = [item["z_index"] for item in items]
        if any(type(index) is not int for index in order) or sorted(order) != list(range(len(items))):
            raise ValueError()
        entries = []
        canvas = None
        for item in sorted(items, key=lambda value: value["z_index"]):
            index = item["z_index"]
            expected = "png" if index else request.output_format
            if item.get("error") is not None or item.get("output_format", expected) != expected:
                raise ValueError()
            size = re.fullmatch(r"([1-9][0-9]{0,3})x([1-9][0-9]{0,3})", item["size"])
            if size is None:
                raise ValueError()
            width, height = map(int, size.groups())
            if width * height > 4624220 or max(width, height) > 16 * min(width, height):
                raise ValueError()
            url = item["url"]
            if not isinstance(url, str) or len(url) > 8192:
                raise ValueError()
            entry = {"z_index": index, "width": width, "height": height, "output_format": expected,
                     "url": validate_provider_reference_url(url)}
            if index == 0:
                canvas = (width, height)
                if item.get("bounding_box") is not None or item.get("text_recognition") is not None:
                    raise ValueError()
            else:
                entry["bounding_box"] = _coordinates(item["bounding_box"], *canvas)
                for field in ("name", "description"):
                    if item.get(field) is not None:
                        entry[field] = _text(item[field], 4096)
                if request.text_recognition and item.get("text_recognition") is not None:
                    entry["text_recognition"] = _ocr(item["text_recognition"], *canvas)
            entries.append(entry)
        if len(canonical_submission_json(entries).encode()) > 2 * 1024 * 1024:
            raise ValueError()
        created = response.get("created")
        if type(created) is int and 0 < created <= received_at.timestamp():
            received_at = datetime.fromtimestamp(created, tz=timezone.utc)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise ProviderProtocolError("BytePlus Seedream 分层结果与合同不符") from None
    return entries, received_at + timedelta(hours=24)
