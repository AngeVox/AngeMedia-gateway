"""校验可作为原始作品交付的静态 SVG。"""
from __future__ import annotations

import math
import re
from xml.parsers import expat


SVG_MAX_BYTES = 2 * 1024 * 1024
SVG_MEDIA_TYPE = "image/svg+xml"
_NS = "http://www.w3.org/2000/svg "
_ROOT = _NS + "svg"
_XML = "http://www.w3.org/XML/1998/namespace "
_MAX_ELEMENTS = 10_000
_MAX_DEPTH = 64
_ELEMENTS = frozenset("""
    svg title desc g defs path rect circle ellipse line polyline polygon text tspan
    linearGradient radialGradient stop clipPath
""".split())
_ATTRIBUTES = frozenset("""
    id class role aria-label aria-labelledby aria-describedby version viewBox width height
    preserveAspectRatio x y x1 y1 x2 y2 cx cy r rx ry fx fy fr offset d points
    transform gradientTransform gradientUnits spreadMethod clipPathUnits
    fill fill-opacity fill-rule stroke stroke-width stroke-opacity stroke-linecap
    stroke-linejoin stroke-miterlimit stroke-dasharray stroke-dashoffset clip-rule
    clip-path opacity display visibility vector-effect paint-order color stop-color stop-opacity
    font-family font-size font-weight font-style font-variant font-stretch text-anchor
    dominant-baseline alignment-baseline baseline-shift letter-spacing word-spacing
    textLength lengthAdjust dx dy rotate shape-rendering text-rendering
""".split()) | {_XML + "lang", _XML + "space"}
_PAINT_ATTRIBUTES = {"fill", "stroke", "color", "stop-color", "clip-path"}
_ID = r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}"
_FRAGMENT = re.compile(rf"url\(\s*(['\"]?)(#{_ID})\1\s*\)")
_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}|[A-Za-z]+|(?:rgb|rgba|hsl|hsla)\([-+0-9.eE% ,/]+\)", re.I)
_ROOT_LENGTH = re.compile(r"(\d{1,5}(?:\.\d{1,8})?|\.\d{1,8})(px|%)?")


class UnsafeSvgOutput(RuntimeError):
    """SVG 超出了静态作品支持的内容范围。"""


def _prohibited(*_args) -> None:
    raise UnsafeSvgOutput("SVG contains unsupported document constructs")


def _xml_declaration(version: str, encoding: str | None, standalone: int) -> None:
    if version != "1.0" or (encoding is not None and encoding.lower() != "utf-8"):
        _prohibited()


def _validate_canvas(attributes: dict[str, str]) -> None:
    for dimension in ("width", "height"):
        value = attributes.get(dimension)
        if value is not None:
            length = _ROOT_LENGTH.fullmatch(value.strip())
            if not length or not 0 < float(length[1]) <= (100 if length[2] == "%" else 8192):
                raise UnsafeSvgOutput("SVG canvas dimensions are outside the preview bounds")
    if "viewBox" not in attributes:
        return
    try:
        sizes = [float(part) for part in re.split(r"[\s,]+", attributes["viewBox"].strip())]
    except ValueError as exc:
        raise UnsafeSvgOutput("SVG viewBox is invalid") from exc
    if len(sizes) != 4 or any(not math.isfinite(value) or abs(value) > 1_000_000 for value in sizes) or min(sizes[2:]) <= 0:
        raise UnsafeSvgOutput("SVG viewBox is outside the preview bounds")


def validate_static_svg(content: bytes) -> None:
    """只允许有界 UTF-8 XML 及静态向量元素，保留原文件字节。"""
    if not 0 < len(content) <= SVG_MAX_BYTES:
        raise UnsafeSvgOutput("SVG exceeds the local size limit")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsafeSvgOutput("SVG must use UTF-8") from exc

    stack: list[str] = []
    ids: dict[str, str] = {}
    references: list[tuple[str, set[str]]] = []
    count = 0

    def start(name: str, attributes: dict[str, str]) -> None:
        nonlocal count
        count += 1
        if len(stack) >= _MAX_DEPTH or count > _MAX_ELEMENTS or len(attributes) > 64:
            raise UnsafeSvgOutput("SVG document structure exceeds the local bounds")
        if not stack:
            if name != _ROOT:
                raise UnsafeSvgOutput("SVG root and namespace are required")
            _validate_canvas(attributes)
        elif name == _ROOT:
            raise UnsafeSvgOutput("Nested SVG canvases are not supported")
        element = name.removeprefix(_NS)
        if not name.startswith(_NS) or element not in _ELEMENTS or attributes.keys() - _ATTRIBUTES:
            raise UnsafeSvgOutput("SVG contains unsupported elements or attributes")
        if "id" in attributes:
            identifier = attributes["id"]
            if not re.fullmatch(_ID, identifier) or identifier in ids:
                raise UnsafeSvgOutput("SVG IDs must be unique local identifiers")
            ids[identifier] = element
        for attribute in _PAINT_ATTRIBUTES.intersection(attributes):
            value = attributes[attribute].strip()
            fragment = _FRAGMENT.fullmatch(value)
            if fragment and attribute in {"fill", "stroke"}:
                references.append((fragment[2][1:], {"linearGradient", "radialGradient"}))
            elif fragment and attribute == "clip-path" and "clipPath" not in stack and element != "clipPath":
                references.append((fragment[2][1:], {"clipPath"}))
            elif (attribute == "clip-path" and value != "none") or (attribute != "clip-path" and not _COLOR.fullmatch(value)):
                raise UnsafeSvgOutput("SVG paint and clip references must stay inside the document")
        stack.append(element)

    parser = expat.ParserCreate(encoding="UTF-8", namespace_separator=" ")
    parser.StartElementHandler = start
    parser.EndElementHandler = lambda _name: stack.pop()
    parser.StartDoctypeDeclHandler = _prohibited
    parser.EntityDeclHandler = _prohibited
    parser.ExternalEntityRefHandler = _prohibited
    parser.ProcessingInstructionHandler = _prohibited
    parser.XmlDeclHandler = _xml_declaration
    try:
        parser.Parse(content, True)
    except expat.ExpatError as exc:
        raise UnsafeSvgOutput("SVG XML is malformed") from exc
    if any(ids.get(target) not in allowed for target, allowed in references):
        raise UnsafeSvgOutput("SVG references an unavailable local vector resource")
