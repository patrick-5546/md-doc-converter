"""Recover image descriptions and publish validated document media."""

from __future__ import annotations

import copy
import hashlib
import html
import re
import unicodedata
import urllib.parse
from collections import Counter, defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from md_doc_converter import executables
from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx.diagnostics import TransformReport
from md_doc_converter.converters.docx.models import (
    ConversionError,
    ConversionPaths,
    ImageDescription,
    WordPackage,
)
from md_doc_converter.converters.docx.ooxml import DRAWING_WORD, OFFICE, VML, WORD

WEB_IMAGE_SUFFIXES = frozenset({".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})


VECTOR_SUFFIXES = frozenset({".emf", ".wmf"})


IMAGE_SUFFIXES = WEB_IMAGE_SUFFIXES | VECTOR_SUFFIXES


MIME_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/tiff": ".tif",
    "image/x-emf": ".emf",
    "image/emf": ".emf",
    "image/x-wmf": ".wmf",
    "image/wmf": ".wmf",
}


_CAPTION_SEPARATORS = ":.-\u2011\u2013\u2014 "
_FIGURE_NUMBER = re.compile(
    r"\s*(?i:figure|fig\.)\s*(?:\d+|[A-Z]|[IVXLC]+)(?:[.\-\u2011\u2013]\d+)*(?![\w\u2011-])"
)
_OTHER_CAPTION = re.compile(r"\s*(?i:table|equation)\s*(?:\d|[A-Z]\b)")


def _caption_title(paragraph: ET.Element) -> str | None:
    """The title of a figure caption, or None when *paragraph* is not one.

    A figure caption is in Word's Caption style or numbered by a SEQ Figure field; table and
    equation captions are not. Its title is the text after the number, or all of its text
    when nothing follows the number.
    """
    pieces: list[str] = []
    fields: list[list[str]] = []
    sequence: str | None = None
    after = 0
    for run in paragraph.iter(WORD + "r"):
        for node in run:
            if node.tag == WORD + "fldChar":
                kind = node.get(WORD + "fldCharType")
                if kind == "begin":
                    fields.append([])
                elif kind == "end" and fields:
                    match = re.match(r"\s*SEQ\s+(\S+)", "".join(fields.pop()), re.IGNORECASE)
                    if match and sequence is None:
                        sequence, after = match.group(1).casefold(), len("".join(pieces))
            elif node.tag == WORD + "instrText" and fields:
                fields[-1].append(node.text or "")
            elif node.tag == WORD + "t":
                pieces.append(node.text or "")
            elif node.tag == WORD + "noBreakHyphen":
                pieces.append("-")
            elif node.tag in {WORD + "tab", WORD + "br", WORD + "cr"}:
                pieces.append(" ")
    text = "".join(pieces)
    style = paragraph.find(f"./{WORD}pPr/{WORD}pStyle")
    styled = style is not None and style.get(WORD + "val", "").casefold() == "caption"
    if sequence in {"table", "equation"} or (sequence not in {None, "figure"} and not styled):
        return None
    if sequence is not None:
        title = text[after:]
    elif styled and not _OTHER_CAPTION.match(text):
        number = _FIGURE_NUMBER.match(text)
        title = text[number.end() :] if number else text
    else:
        return None
    title = " ".join(title.split()).lstrip(_CAPTION_SEPARATORS)
    text = " ".join(text.split())
    return title if re.search(r"\w", title) else text or None


def image_descriptions(
    root: ET.Element,
    units: list[ET.Element] | None = None,
) -> list[ImageDescription]:
    """Describe each image by its figure caption, or else by Word's alt text.

    A caption is the nearest paragraph with text above or below a paragraph with one image,
    in the same table cell or body. An image with a caption on both sides takes the one on
    the side where most of the document's captions are, and each caption describes one image.
    """
    parents = {child: parent for parent in root.iter() for child in parent}
    paragraphs = list(_ooxml.scoped_iter(root, WORD + "p"))
    paragraph_indices = {paragraph: index for index, paragraph in enumerate(paragraphs)}
    drawings = _ooxml.image_units(root) if units is None else units

    def ancestor(node: ET.Element, tags: set[str]) -> ET.Element | None:
        current: ET.Element | None = node
        while current is not None and current.tag not in tags:
            current = parents.get(current)
        return current

    image_paragraphs = [ancestor(drawing, {WORD + "p"}) for drawing in drawings]
    counts = Counter(image_paragraphs)
    containers: dict[ET.Element | None, list[ET.Element]] = defaultdict(list)
    for paragraph in paragraphs:
        containers[ancestor(paragraph, {WORD + "tc", WORD + "body"})].append(paragraph)

    titles: dict[ET.Element, str | None] = {}
    metadata: list[str | None] = []
    captions: list[dict[int, ET.Element]] = []
    for drawing, paragraph in zip(drawings, image_paragraphs, strict=True):
        properties = next(drawing.iter(DRAWING_WORD + "docPr"), None)
        values = (
            [properties.get(key, "") for key in ("descr", "title")]
            if properties is not None
            else []
        )
        values.extend(
            node.get(key, "")
            for node in drawing.iter()
            if node.tag in {VML + "shape", VML + "imagedata"}
            for key in ("alt", "title", OFFICE + "title")
        )
        metadata.append(
            next((re.sub(r"\s+", " ", value).strip() for value in values if value.strip()), None)
        )
        found: dict[int, ET.Element] = {}
        if paragraph is not None and counts[paragraph] == 1:
            siblings = containers[ancestor(paragraph, {WORD + "tc", WORD + "body"})]
            position = siblings.index(paragraph)
            for direction in (-1, 1):
                index = position + direction
                while 0 <= index < len(siblings):
                    sibling = siblings[index]
                    if _ooxml.paragraph_text(sibling) or counts[sibling]:
                        if sibling not in titles:
                            titles[sibling] = _caption_title(sibling)
                        if titles[sibling] is not None:
                            found[direction] = sibling
                        break
                    index += direction
        captions.append(found)
    sides = Counter(side for found in captions if len(found) == 1 for side in found)
    preferred = 1 if sides[1] >= sides[-1] else -1
    chosen: dict[int, ET.Element] = {}
    for side in (preferred, -preferred):
        claims = Counter(
            found[side]
            for index, found in enumerate(captions)
            if index not in chosen and side in found
        )
        taken = set(chosen.values())
        for index, found in enumerate(captions):
            caption = found.get(side)
            if index in chosen or caption is None or caption in taken or claims[caption] > 1:
                continue
            chosen[index] = caption
            taken.add(caption)
    result: list[ImageDescription] = []
    for index, (paragraph, description) in enumerate(zip(image_paragraphs, metadata, strict=True)):
        title = titles[chosen[index]] if index in chosen else None
        result.append(
            ImageDescription(
                title or description,
                paragraph_indices[paragraph] if paragraph is not None else -1,
                from_caption=title is not None,
            )
        )
    return result


def image_url(name: PurePosixPath, paths: ConversionPaths) -> str:
    return urllib.parse.quote(
        (PurePosixPath(".images") / paths.output.stem / name).as_posix(), safe="/"
    )


def escape_image_alt_text(value: str) -> str:
    text = html.escape(value, quote=False).replace("\n", " ")
    return re.sub(r"([\\`*_\[\]|])", r"\\\1", text)


def validate_svg(path: Path) -> ET.Element:
    """Check the SVG at *path*, and return its root."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        raise ConversionError(f"Invalid SVG {path}: {error}") from error
    _validate_svg_root(root, path)
    return root


def validate_svg_bytes(data: bytes, label: str) -> None:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ConversionError(f"Invalid SVG {label}: {error}") from error
    _validate_svg_root(root, label)


def _validate_svg_root(root: ET.Element, path: Path | str) -> None:
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ConversionError(f"SVG has no <svg> root: {path}")
    ids = [node.get("id", "") for node in root.iter() if node.get("id")]
    if len(ids) != len(set(ids)):
        raise ConversionError(f"Duplicate SVG fragment identities in {path}")
    targets: list[str] = []
    for node in root.iter():
        name = node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else ""
        handlers = [
            key for key in node.attrib if key.rsplit("}", 1)[-1].casefold().startswith("on")
        ]
        # A script would run where the SVG is opened as a page rather than shown as an image.
        if name == "script" or handlers:
            raise ConversionError(f"SVG contains script: {path}")
    css_attributes = {
        "style",
        "clip-path",
        "fill",
        "stroke",
        "filter",
        "mask",
        "cursor",
        "marker",
        "marker-start",
        "marker-mid",
        "marker-end",
    }
    for node in root.iter():
        for attribute, value in node.attrib.items():
            name = attribute.rsplit("}", 1)[-1]
            if name == "href":
                targets.append(value)
            if name in css_attributes:
                targets.extend(
                    quoted or unquoted.strip()
                    for quoted, unquoted in re.findall(
                        r"""url\(\s*(?:["']([^"']*)["']|([^)]*))\s*\)""", value
                    )
                )
        if node.tag.rsplit("}", 1)[-1] == "style":
            targets.extend(
                quoted or unquoted.strip()
                for quoted, unquoted in re.findall(
                    r"""url\(\s*(?:["']([^"']*)["']|([^)]*))\s*\)""", node.text or ""
                )
            )
    for value in targets:
        if value.startswith("#") and urllib.parse.unquote(value[1:]) in ids:
            continue
        if value.startswith("data:") and not value.endswith("..."):
            continue
        raise ConversionError(f"Unresolved SVG resource in {path}: {value!r}")


# LibreOffice draws an EMF or WMF file in the middle of a default page, such as US Letter, and
# exports the whole page as a tagged PDF, whose one Figure gives the drawing's frame; pdftocairo
# turns the page into an SVG, which is cropped back to the frame. LibreOffice's PDF export draws
# a drawing as Word does far more often than its SVG export, which misplaces rotated text, drops
# repeated bitmaps and fails on some drawings outright. pdftocairo draws text as outlines, which
# look the same everywhere; an invisible copy of the words keeps them searchable.
Frame = tuple[float, float, float, float]
_LENGTH = re.compile(r"\s*([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*(mm|cm|in|pt|pc|px)?\s*")
# LibreOffice 7.4 and newer read the export's options as JSON; older ones ignore them.
_TAGGED_PDF = 'pdf:draw_pdf_Export:{"UseTaggedPDF":{"type":"boolean","value":"true"}}'
_NUMBER = r"[-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?"
# A frame as `pdfinfo -struct` prints it: left, bottom, right and top, up from the page's bottom.
_BBOX = re.compile(rf"^\s*/BBox \[({_NUMBER}) ({_NUMBER}) ({_NUMBER}) ({_NUMBER})\]", re.MULTILINE)
_XHTML = "{http://www.w3.org/1999/xhtml}"
# Characters that XML cannot hold, which a PDF may still map a glyph to.
_NOT_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
# The Unicode categories of characters that no font draws: private use, such as the codes of
# Word's Symbol and Wingdings fonts, unassigned, and surrogates.
_UNDRAWN = frozenset({"Co", "Cn", "Cs"})


def _figure_frame(structure: str, view_box: str | None) -> Frame | str:
    """The drawing's frame from *structure*, which `pdfinfo -struct` printed of LibreOffice's
    tagged PDF, in the user units of the page's SVG, whose viewBox is *view_box*; or why it is
    unknown.

    The PDF measures up from the bottom of the page, the SVG down from the top. LibreOffice puts
    the drawing in the middle of the page, so a frame that is not there was misread.
    """
    boxes = _BBOX.findall(structure)
    if not boxes:
        return "LibreOffice did not report the drawing's frame, as LibreOffice 7.4 and newer do"
    if len(boxes) > 1:
        return "LibreOffice reported more than one frame"
    try:
        left, top, width, height = (
            float(part) for part in re.split(r"[\s,]+", (view_box or "").strip())
        )
    except ValueError:
        return "the page's viewBox is not four numbers"
    x0, y0, x1, y1 = (float(value) for value in boxes[0])
    # Allow for rounding.
    if abs(x0 - (width - x1)) > 1 or abs(y0 - (height - y1)) > 1:
        return "the drawing's frame is not in the middle of the page"
    return left + x0, top + height - y1, x1 - x0, y1 - y0


def _crop_svg(path: Path, frame: Frame) -> str | None:
    """Crop the SVG page at *path* to *frame*, in user units; return why it could not be
    cropped."""
    try:
        # Its line endings are kept as pdftocairo wrote them.
        text = path.read_text(encoding="utf-8", newline="")
    except (OSError, UnicodeDecodeError) as error:
        return f"the SVG could not be read: {error}"
    start = re.search(r"<svg\b[^>]*>", text)
    if start is None:
        return "the SVG has no <svg> start tag"
    tag = start.group(0)
    spans: dict[str, tuple[int, int]] = {}
    for name in ("width", "height", "viewBox"):
        match = re.search(rf"(?<=\s){name}\s*=\s*([\"'])(.*?)\1", tag)
        if match is None:
            return f"the page has no {name}"
        spans[name] = match.span(2)
    sizes = [_LENGTH.fullmatch(tag[slice(*spans[name])]) for name in ("width", "height")]
    try:
        left, top, page_width, page_height = (
            float(part) for part in re.split(r"[\s,]+", tag[slice(*spans["viewBox"])].strip())
        )
    except ValueError:
        return "the page's viewBox is not four numbers"
    if sizes[0] is None or sizes[1] is None or page_width <= 0 or page_height <= 0:
        return "the page's size is not a length"
    x, y, width, height = frame
    # Allow for rounding at the page's edges, but never cut into a drawing.
    slack_x, slack_y = page_width / 100, page_height / 100
    if (
        width <= 0
        or height <= 0
        or x < left - slack_x
        or y < top - slack_y
        or x + width > left + page_width + slack_x
        or y + height > top + page_height + slack_y
    ):
        return "the drawing's frame is not on the page"
    x0, y0 = max(x, left), max(y, top)
    x1, y1 = min(x + width, left + page_width), min(y + height, top + page_height)

    def number(value: float) -> str:
        return f"{value:.4f}".rstrip("0").rstrip(".")

    values = {
        "viewBox": " ".join(number(value) for value in (x0, y0, x1 - x0, y1 - y0)),
        "width": number(float(sizes[0].group(1)) * (x1 - x0) / page_width)
        + (sizes[0].group(2) or ""),
        "height": number(float(sizes[1].group(1)) * (y1 - y0) / page_height)
        + (sizes[1].group(2) or ""),
    }
    for begin, end, name in sorted(((*span, name) for name, span in spans.items()), reverse=True):
        tag = tag[:begin] + values[name] + tag[end:]
    path.write_text(text[: start.start()] + tag + text[start.end() :], encoding="utf-8", newline="")
    return None


_UNITLESS_SIZE = re.compile(
    r"""(?<=\s)(width|height)(\s*=\s*)(["'])\s*([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*\3"""
)


def _size_in_points(path: Path) -> None:
    """Give the page size of a pdftocairo SVG the unit `pt` when it has none.

    pdftocairo draws in PDF points, but only Poppler 25.04 and newer write the unit. With an older
    Poppler and Cairo 1.17.6 or newer, the size has no unit, which SVG reads as CSS pixels, so the
    drawing would show at 75% of its size.
    """
    try:
        # Its line endings are kept as pdftocairo wrote them.
        text = path.read_text(encoding="utf-8", newline="")
    except (OSError, UnicodeDecodeError) as error:
        raise ConversionError(f"PDF-to-SVG conversion's SVG could not be read: {error}") from error
    start = re.search(r"<svg\b[^>]*>", text)
    if start is None:
        return
    tag = _UNITLESS_SIZE.sub(
        lambda size: f"{size[1]}{size[2]}{size[3]}{size[4]}pt{size[3]}", start.group(0)
    )
    path.write_text(text[: start.start()] + tag + text[start.end() :], encoding="utf-8", newline="")


# A converted drawing's SVG, or its PNG, or why it could not be converted, and notes on the
# conversion.
VectorResult = tuple[bytes | str, list[str]]
# The notes: the drawing could not be cut from LibreOffice's page, or it is published as a PNG,
# which the Markdown shows; or its words could not be kept searchable, a detail.
_WHOLE_PAGE = "kept LibreOffice's whole page around the drawing"
_DRAWN_AS_PNG = "published as a PNG, as its SVG would be too large; its text is not searchable"
_UNSEARCHABLE = "its text is not searchable"
# Azure DevOps wikis remove the <use> elements of the SVGs they show, and pdftocairo draws each
# letter and picture of a drawing with one, so a wiki would show its lines without its text.
_KEPT_USES = "its SVG keeps <use> elements, whose text and pictures Azure DevOps wikis do not show"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# LibreOffice can draw some drawings, such as dotted lines in EMF+, as countless separate dots,
# whose SVG is then hundreds of times the size of the drawing: too large to publish or to show.
# An SVG that is more than this many times the size of its drawing, and over a mebibyte, gives
# way to a PNG of the same part of the page, at twice the resolution of Word's 100% zoom.
_SVG_GROWTH = 20
_LARGE_SVG = 1 << 20
_PNG_RESOLUTION = 192
# Azure DevOps wikis show an SVG only through a sanitizer, which refuses one over 5 MB; such an
# SVG, without its <use> elements, gives way to a PNG too.
_WIKI_SVG_SIZE = 5_000_000


def _page_png(pdf: Path, svg: bytes, work: Path, cairo: str) -> bytes:
    """A PNG of the part of LibreOffice's page *pdf* that the SVG *svg* of the page shows."""
    start = re.search(rb"<svg\b[^>]*>", svg)
    view_box = re.search(rb"""(?<=\s)viewBox\s*=\s*(["'])(.*?)\1""", start[0]) if start else None
    numbers = (
        re.split(r"[\s,]+", view_box[2].decode("ascii", "replace").strip()) if view_box else []
    )
    try:
        left, top, width, height = (float(part) for part in numbers)
    except ValueError as error:
        raise ConversionError("the drawing's viewBox is not four numbers") from error
    # pdftocairo's SVG measures the page in points, and the PNG in pixels.
    scale = _PNG_RESOLUTION / 72
    root = work / f"{pdf.stem}-png"
    _reference.run_command(
        [
            cairo,
            "-png",
            "-singlefile",
            "-r",
            str(_PNG_RESOLUTION),
            "-x",
            str(round(left * scale)),
            "-y",
            str(round(top * scale)),
            "-W",
            str(max(1, round(width * scale))),
            "-H",
            str(max(1, round(height * scale))),
            str(pdf),
            str(root),
        ],
        "PDF-to-PNG conversion",
        quiet=True,
    )
    png = root.with_name(f"{root.name}.png")
    if not _produced(png):
        raise ConversionError("PDF-to-PNG conversion produced no PNG")
    return png.read_bytes()


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _vertical(boxes: list[tuple[float, float, float, float, str]]) -> bool:
    """Whether the words in *boxes*, a line of them, read up or down rather than across."""
    if len(boxes) > 1:
        # Vertical words stack in a column; horizontal words stand side by side.
        return max(box[0] for box in boxes) < min(box[2] for box in boxes)
    left, top, right, bottom, text = boxes[0]
    return bottom - top > 1.8 * (right - left) and len(text) > 2


def _text_layer(words: str) -> str:
    """An invisible SVG group of the words that `pdftotext -bbox-layout` found, given as its
    XHTML *words*, each over the place where it is drawn; or "" if there are none.

    A line is a text element whose words are stretched over their boxes, so that a PDF of the
    page finds and selects them where they show. A line's box spans its font's ascent and
    descent: the baseline is about a fifth of the way from the descent's side, and the font's
    size about 85% of the box across the line. A vertical line turns to read up the page, or
    down when its words go down. Characters that no font draws are left out: a PDF of the page
    would show a missing glyph for each.
    """
    group = ET.Element("g", {"fill-opacity": "0", "font-family": "sans-serif"})
    for line in ET.fromstring(_NOT_XML.sub("", words)).iter(_XHTML + "line"):
        boxes: list[tuple[float, float, float, float, str]] = []
        for word in line.iter(_XHTML + "word"):
            drawn = "".join(
                character
                for character in word.text or ""
                if unicodedata.category(character) not in _UNDRAWN
            )
            text = " ".join(drawn.split())
            try:
                left, top, right, bottom = (
                    float(word.get(edge, "")) for edge in ("xMin", "yMin", "xMax", "yMax")
                )
            except ValueError:
                continue
            if text and right > left and bottom > top:
                boxes.append((left, top, right, bottom, text))
        if not boxes:
            continue
        left, top = min(box[0] for box in boxes), min(box[1] for box in boxes)
        right, bottom = max(box[2] for box in boxes), max(box[3] for box in boxes)
        # Each word's start along the line, its length and its text.
        spans: list[tuple[float, float, str]]
        if not _vertical(boxes):
            size = bottom - top
            place = {"y": _number(bottom - 0.22 * size)}
            spans = [(box[0], box[2] - box[0], box[4]) for box in boxes]
        elif len(boxes) == 1 or boxes[0][1] > boxes[-1][1]:
            size = right - left
            place = {
                "transform": f"translate({_number(right - 0.22 * size)} {_number(bottom)}) "
                "rotate(-90)"
            }
            spans = [(bottom - box[3], box[3] - box[1], box[4]) for box in boxes]
        else:
            size = right - left
            place = {
                "transform": f"translate({_number(left + 0.22 * size)} {_number(top)}) rotate(90)"
            }
            spans = [(box[1] - top, box[3] - box[1], box[4]) for box in boxes]
        element = ET.SubElement(group, "text", {**place, "font-size": _number(0.85 * size)})
        for index, (start, length, text) in enumerate(spans):
            span = ET.SubElement(
                element,
                "tspan",
                {
                    "x": _number(start),
                    "textLength": _number(length),
                    "lengthAdjust": "spacingAndGlyphs",
                },
            )
            span.text = text
            # Words apart, so that a phrase can be found.
            span.tail = " " if index < len(spans) - 1 else None
    return ET.tostring(group, encoding="unicode") if len(group) else ""


def _add_text_layer(svg: Path, words: Path) -> None:
    """Add the invisible layer of the words that pdftotext wrote to *words* to *svg*."""
    layer = _text_layer(words.read_text(encoding="utf-8", errors="replace"))
    if not layer:
        return
    # Its line endings are kept as pdftocairo wrote them.
    text = svg.read_text(encoding="utf-8", newline="")
    end = text.rfind("</svg>")
    if end < 0:
        raise ConversionError(f"SVG has no </svg> end tag: {svg}")
    svg.write_text(text[:end] + layer + text[end:], encoding="utf-8", newline="")


def _produced(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _page_svg(
    pdf: Path, work: Path, findings: list[str], *, cairo: str, info: str, pdftotext: str | None
) -> bytes:
    """The SVG of LibreOffice's page *pdf*, cropped to its drawing, with its words searchable.

    It is written in *work*; what could not be done is noted in *findings*.
    """
    svg, words = work / f"{pdf.stem}.svg", work / f"{pdf.stem}.html"
    # Its warnings concern our intermediate PDF, which users cannot act on.
    _reference.run_command([cairo, "-svg", str(pdf), str(svg)], "PDF-to-SVG conversion", quiet=True)
    if not _produced(svg):
        raise ConversionError("PDF-to-SVG conversion produced no SVG")
    _size_in_points(svg)
    view_box = validate_svg(svg).get("viewBox")
    try:
        structure = _reference.run_command(
            [info, "-struct", str(pdf)], "EMF frame lookup", quiet=True
        )
    except ConversionError as error:
        problem: str | None = str(error)
    else:
        frame = _figure_frame(structure, view_box)
        problem = frame if isinstance(frame, str) else _crop_svg(svg, frame)
    if problem is not None:
        findings.append(f"{_WHOLE_PAGE}: {problem}")
    if pdftotext is None:
        findings.append(f"{_UNSEARCHABLE}: Poppler's pdftotext is not installed")
    else:
        try:
            _reference.run_command(
                [pdftotext, "-bbox-layout", "-enc", "UTF-8", str(pdf), str(words)],
                "EMF text lookup",
                quiet=True,
            )
            _add_text_layer(svg, words)
        except (ConversionError, ET.ParseError, OSError, ValueError) as error:
            findings.append(f"{_UNSEARCHABLE}: {error}")
    validate_svg(svg)
    return svg.read_bytes()


_SVG = "{http://www.w3.org/2000/svg}"
_XLINK = "{http://www.w3.org/1999/xlink}"
_XML = "{http://www.w3.org/XML/1998/namespace}"
_HREFS = frozenset({_XLINK + "href", "href"})
# The attributes of a <use> that place what it shows, rather than apply to it.
_PLACEMENT = _HREFS | {"x", "y", "width", "height", "transform"}
_URL_REFERENCE = re.compile(r"""url\(\s*["']?#([^"')\s]+)""")
# How many <use> elements deep one may show another.
_USE_DEPTH = 8


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _shown_by(
    use: ET.Element, ids: dict[str, ET.Element], parents: dict[ET.Element, ET.Element]
) -> list[ET.Element] | str:
    """Copies of what *use* shows, placed where it shows them; or why they cannot be."""
    reference = next((value for key, value in use.attrib.items() if key in _HREFS), "")
    target = ids.get(urllib.parse.unquote(reference[1:])) if reference.startswith("#") else None
    if target is None:
        return f"a <use> shows {reference!r}, which is not in the SVG"
    container: ET.Element | None = use
    while container is not None:
        if container is target:
            return "a <use> shows what contains it"
        container = parents.get(container)
    name = _local(target.tag)
    # A symbol is drawn in a viewport of its own, which clips it unless its overflow shows.
    if name == "svg" or (
        name == "symbol"
        and (target.get("viewBox") is not None or target.get("overflow") != "visible")
    ):
        return f"a <use> shows a <{name}> that clips what it shows"
    x, y = (use.get(axis, "0").strip() for axis in ("x", "y"))
    if not (re.fullmatch(_NUMBER, x) and re.fullmatch(_NUMBER, y)):
        return f"a <use> is placed at {x!r}, {y!r} rather than at numbers"
    move = f"translate({x},{y})" if float(x) or float(y) else ""
    transform = " ".join(part for part in (use.get("transform"), move) if part)
    shows = list(target) if name == "symbol" else [target]
    copies: list[ET.Element] = []
    for node in shows:
        # A path without data, as pdftocairo writes for a space, draws nothing.
        if _local(node.tag) == "path" and not node.get("d", "").strip():
            continue
        shown = copy.deepcopy(node)
        shown.tail = None
        for part in shown.iter():
            part.attrib.pop("id", None)
        copies.append(shown)
    others = {key: value for key, value in use.attrib.items() if key not in _PLACEMENT}
    if not others:
        # A group that only moves what it holds is the same as moving each part.
        for shown in copies:
            if transform:
                shown.set("transform", " ".join(filter(None, (transform, shown.get("transform")))))
        placed = copies
    elif _local(parents[use].tag) == "clipPath":
        return "a <use> in a clipping path has attributes of its own"
    else:
        group = ET.Element(_SVG + "g", others)
        if transform:
            group.set("transform", transform)
        group.extend(copies)
        placed = [group]
    if placed:
        placed[-1].tail = use.tail
    return placed


def _references(root: ET.Element) -> set[str]:
    """The fragment identities that the SVG *root* refers to."""
    names: set[str] = set()
    for node in root.iter():
        for key, value in node.attrib.items():
            if key in _HREFS:
                if value.startswith("#"):
                    names.add(urllib.parse.unquote(value[1:]))
            elif "url(" in value:
                names.update(urllib.parse.unquote(name) for name in _URL_REFERENCE.findall(value))
    return names


def _drop_unshown_definitions(root: ET.Element) -> None:
    """Remove the symbols and definitions of the SVG *root* that nothing refers to any longer."""
    while True:
        referenced = _references(root)
        parents = {child: parent for parent in root.iter() for child in parent}
        dropped = False
        for node in list(root.iter()):
            parent = parents.get(node)
            if parent is None:
                continue
            # pdftocairo groups its symbols in a <g> of their own in <defs>.
            holder = (
                parents.get(parent) if parent.tag == _SVG + "g" and not parent.attrib else parent
            )
            if node.tag != _SVG + "symbol" and (holder is None or holder.tag != _SVG + "defs"):
                continue
            if any(part.get("id") in referenced for part in node.iter() if part.get("id")):
                continue
            parent.remove(node)
            dropped = True
        if not dropped:
            break
    parents = {child: parent for parent in root.iter() for child in parent}
    for definitions in list(root.iter(_SVG + "defs")):
        for group in list(definitions):
            if group.tag == _SVG + "g" and not group.attrib and not len(group):
                definitions.remove(group)
        if not len(definitions) and definitions in parents:
            parents[definitions].remove(definitions)


def _xml_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _xml_attribute(value: str) -> str:
    return (
        _xml_text(value)
        .replace('"', "&quot;")
        .replace("\n", "&#10;")
        .replace("\r", "&#13;")
        .replace("\t", "&#9;")
    )


def _svg_bytes(root: ET.Element) -> bytes:
    """The SVG *root* as a file, with the usual prefixes of its namespaces.

    ElementTree would name the namespaces that no prefix is registered for ns0 and ns1, and the
    registered prefixes are shared by the whole process.
    """
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n']

    def qualified(name: str) -> str:
        for namespace, prefix in ((_SVG, ""), (_XLINK, "xlink:"), (_XML, "xml:")):
            if name.startswith(namespace):
                return prefix + name[len(namespace) :]
        return name

    def write(node: ET.Element, declarations: str = "") -> None:
        tag = qualified(node.tag)
        parts.append(f"<{tag}{declarations}")
        parts.extend(f' {qualified(key)}="{_xml_attribute(value)}"' for key, value in node.items())
        if node.text or len(node):
            parts.append(">")
            if node.text:
                parts.append(_xml_text(node.text))
            for child in node:
                write(child)
            parts.append(f"</{tag}>")
        else:
            parts.append("/>")
        if node.tail:
            parts.append(_xml_text(node.tail))

    write(root, f' xmlns="{_SVG[1:-1]}" xmlns:xlink="{_XLINK[1:-1]}"')
    parts.append("\n")
    return "".join(parts).encode("utf-8")


def _without_uses(svg: bytes) -> bytes | str:
    """*svg* with each <use> element replaced by a copy of what it shows, and the definitions
    that nothing shows any longer left out; or why it could not be done."""
    if b"<use" not in svg:
        return svg
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as error:
        return f"it could not be read: {error}"
    for node in root.iter():
        if not node.tag.startswith(_SVG) or any(
            key.startswith("{") and not key.startswith((_XLINK, _XML)) for key in node.attrib
        ):
            return "it has elements or attributes that are not SVG's"
        # A style sheet's selectors could pick out what a <use> shows.
        if node.tag == _SVG + "style":
            return "it has a style sheet"
    ids = {node.get("id", ""): node for node in root.iter() if node.get("id")}
    for _depth in range(_USE_DEPTH):
        parents = {child: parent for parent in root.iter() for child in parent}
        uses = list(root.iter(_SVG + "use"))
        if not uses:
            break
        placed: dict[ET.Element, list[ET.Element]] = {}
        for use in uses:
            shown = _shown_by(use, ids, parents)
            if isinstance(shown, str):
                return shown
            placed[use] = shown
        for parent in {parents[use] for use in uses}:
            parent[:] = [node for child in parent for node in placed.get(child, [child])]
    if next(root.iter(_SVG + "use"), None) is not None:
        return f"its <use> elements show others more than {_USE_DEPTH} deep"
    _drop_unshown_definitions(root)
    try:
        result = _svg_bytes(root)
        validate_svg_bytes(result, "the SVG without <use> elements")
    except (ConversionError, RecursionError) as error:
        return str(error) or "its elements nest too deeply"
    # Its line endings are kept as pdftocairo wrote them.
    return result.replace(b"\n", b"\r\n") if b"\r\n" in svg else result


def _convert_vectors(drawings: dict[str, tuple[bytes, str]], work: Path) -> dict[str, VectorResult]:
    """Convert EMF/WMF drawings, by name, to SVGs cropped to each drawing, or each to a PNG of
    the same part of the page where its SVG would be too large.

    One LibreOffice run with one profile exports every drawing as a tagged PDF; Poppler turns
    each into an SVG with pdftocairo, finds its frame with pdfinfo and its words with pdftotext.
    Each <use> element of an SVG, which Azure DevOps wikis remove, is then replaced by a copy of
    what it shows.
    """
    office = executables.libreoffice()
    cairo, info = executables.poppler("pdftocairo"), executables.poppler("pdfinfo")
    if office is None:
        missing = "EMF conversion requires LibreOffice/soffice; other images do not"
        return {name: (missing, []) for name in drawings}
    if cairo is None or info is None:
        tools = " and ".join(
            name for name, path in (("pdftocairo", cairo), ("pdfinfo", info)) if path is None
        )
        missing = f"EMF conversion requires Poppler's {tools}; other images do not"
        return {name: (missing, []) for name in drawings}
    pdftotext = executables.poppler("pdftotext")
    sources, pdfs, svgs = work / "sources", work / "pdf", work / "svg"
    sources.mkdir(parents=True)
    pdfs.mkdir()
    svgs.mkdir()
    paths: dict[str, Path] = {}
    for name, (data, suffix) in drawings.items():
        paths[name] = sources / f"{name}{suffix}"
        paths[name].write_bytes(data)
    failure: ConversionError | None = None
    try:
        # LibreOffice's warnings on success (such as a missing Java runtime) are not shown.
        _reference.run_command(
            [
                office,
                f"-env:UserInstallation={(work / 'profile').as_uri()}",
                "--headless",
                "--convert-to",
                _TAGGED_PDF,
                "--outdir",
                str(pdfs),
                *(str(path) for path in paths.values()),
            ],
            "EMF-to-PDF conversion",
            quiet=True,
        )
    except ConversionError as error:
        # The drawings that it exported still convert.
        failure = error
    results: dict[str, VectorResult] = {}
    for name, (data, _suffix) in drawings.items():
        pdf = pdfs / f"{name}.pdf"
        findings: list[str] = []
        try:
            if not _produced(pdf):
                raise failure or ConversionError("LibreOffice exported no PDF of the drawing")
            svg = _page_svg(pdf, svgs, findings, cairo=cairo, info=info, pdftotext=pdftotext)
        except (ConversionError, OSError) as error:
            results[name] = (str(error), findings)
            continue
        kept: str | None = None
        if len(svg) > max(_LARGE_SVG, _SVG_GROWTH * len(data)):
            size: str | None = (
                f"{len(svg) / (1 << 20):.0f} MiB, {len(svg) // len(data)} times its drawing"
            )
        else:
            shown = _without_uses(svg)
            if isinstance(shown, str):
                kept = f"{_KEPT_USES}: {shown}"
            else:
                svg = shown
            size = (
                f"{len(svg) / 1e6:.1f} MB, more than the {_WIKI_SVG_SIZE / 1e6:g} MB that "
                "Azure DevOps wikis show"
                if len(svg) > _WIKI_SVG_SIZE
                else None
            )
        if size is not None:
            try:
                svg = _page_png(pdf, svg, svgs, cairo)
            except (ConversionError, OSError) as error:
                findings.append(f"its SVG is {size}, and could not be drawn as a PNG: {error}")
            else:
                findings.append(f"{_DRAWN_AS_PNG} (an SVG of {size})")
                # The PNG shows what the SVG's <use> elements would not.
                kept = None
        if kept is not None:
            findings.append(kept)
        results[name] = (svg, findings)
    return results


def media_name(part: str, folder: str) -> PurePosixPath:
    """The name an image part is published under: relative to the media *folder* if in it."""
    name = PurePosixPath(part)
    return name.relative_to(folder) if name.is_relative_to(folder) else name


class ImageStager:
    """Publish each distinct image once under a unique name, converting EMF/WMF to SVG, or to
    PNG where its SVG would be too large.

    LibreOffice starts converting the drawings that the document's body shows when the stager
    is created, in the background, while the document is read; `close` waits for it.
    """

    def __init__(self, work: Path, package: WordPackage) -> None:
        self.root = work / "final-images"
        self._cache = work / "emf-cache"
        self._runs = 0
        self._parts: dict[str, str] = {}
        self._media = package.media_folder
        # The document's EMF/WMF drawings by digest. Those that the body shows convert in the
        # background; any other, such as one in a footnote, when it is published.
        self._vectors: dict[str, tuple[bytes, str]] = {}
        shown = set(_ooxml.related_parts(package.relationships, package.document_part, "image"))
        background: dict[str, tuple[bytes, str]] = {}
        for name, data in package.parts.items():
            suffix = PurePosixPath(name).suffix.casefold()
            if not data or suffix not in IMAGE_SUFFIXES:
                continue
            digest = hashlib.sha256(data).hexdigest()
            current = self._parts.get(digest)
            if current is None or (
                PurePosixPath(name).is_relative_to(self._media)
                and not PurePosixPath(current).is_relative_to(self._media)
            ):
                self._parts[digest] = name
            if suffix in VECTOR_SUFFIXES:
                self._vectors.setdefault(digest, (data, suffix))
                if name in shown:
                    background.setdefault(digest, (data, suffix))
        self._converted: dict[str, bytes | str] = {}
        self._findings: dict[str, list[str]] = {}
        self._published: dict[str, PurePosixPath] = {}
        self._names: set[str] = set()
        self._data = 0
        self.root.mkdir()
        # How many drawings LibreOffice converts in the background.
        self.drawings = len(background)
        self._background: Future[dict[str, VectorResult]] | None = None
        if background:
            self._runs += 1
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="libreoffice")
            self._background = executor.submit(
                _convert_vectors, background, self._cache / f"run-{self._runs}"
            )
            executor.shutdown(wait=False)

    def close(self) -> None:
        """Wait for the background conversion, which writes in the work folder, to end."""
        if self._background is not None:
            wait([self._background])

    @property
    def count(self) -> int:
        return len(self._published)

    def publish(
        self, key: str, data: bytes, preferred: PurePosixPath, report: TransformReport
    ) -> PurePosixPath:
        if key in self._published:
            return self._published[key]
        suffix = preferred.suffix.casefold()
        content = data
        if suffix in VECTOR_SUFFIXES:
            converted = self._convert(data, suffix, preferred, report)
            if converted is not None:
                drawn = ".png" if converted.startswith(PNG_SIGNATURE) else ".svg"
                content, preferred = converted, preferred.with_suffix(drawn)
        elif suffix == ".svg":
            try:
                validate_svg_bytes(data, preferred.as_posix())
            except ConversionError as error:
                report.skipped(
                    "checks",
                    "Word SVG has unresolved resources or is malformed; published as-is",
                    str(error),
                )
        elif suffix not in WEB_IMAGE_SUFFIXES:
            report.skipped(
                "image-conversion",
                f"{suffix or 'unknown'} images are not converted; published as-is",
                f"{preferred.as_posix()}: browsers may not display {suffix or 'this'} images",
            )
        name = self._unique(preferred, report)
        destination = self.root.joinpath(*name.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        self._published[key] = name
        return name

    def publish_data(
        self, mime: str, data: bytes, report: TransformReport, suffix: str = ""
    ) -> PurePosixPath:
        part = self._parts.get(hashlib.sha256(data).hexdigest())
        if part is not None:
            return self.publish("part:" + part, data, media_name(part, self._media), report)
        key = "data:" + hashlib.sha256(data).hexdigest()
        if key not in self._published:
            self._data += 1
        extension = MIME_SUFFIXES.get(mime.casefold(), suffix.casefold() or ".bin")
        return self.publish(
            key, data, PurePosixPath(f"converted-image{self._data}{extension}"), report
        )

    def _unique(self, preferred: PurePosixPath, report: TransformReport) -> PurePosixPath:
        name, ordinal = preferred, 1
        while name.as_posix().casefold() in self._names:
            ordinal += 1
            name = preferred.with_name(f"{preferred.stem}-{ordinal}{preferred.suffix}")
        if name != preferred:
            report.warn(
                f"Image name {preferred.as_posix()} is already used; published as {name}",
                section="detail",
            )
        self._names.add(name.as_posix().casefold())
        return name

    def _convert(
        self, data: bytes, suffix: str, preferred: PurePosixPath, report: TransformReport
    ) -> bytes | None:
        digest = hashlib.sha256(data).hexdigest()
        if self._background is not None:
            self._store(self._background.result())
            self._background = None
        if digest not in self._converted:
            # Every drawing not yet converted converts in the same LibreOffice run.
            pending = {
                key: value for key, value in self._vectors.items() if key not in self._converted
            }
            pending[digest] = (data, suffix)
            self._runs += 1
            self._store(_convert_vectors(pending, self._cache / f"run-{self._runs}"))
        cached = self._converted[digest]
        label = suffix[1:].upper()
        for message in self._findings.pop(digest, []):
            kind = next(
                (
                    kind
                    for kind in (_WHOLE_PAGE, _DRAWN_AS_PNG, _KEPT_USES)
                    if message.startswith(kind)
                ),
                None,
            )
            if kind is not None:
                report.warn(f"{preferred.as_posix()}: {message}", kind=kind, section="markdown")
            else:
                report.warn(f"{preferred.as_posix()}: {message}", section="detail")
        if isinstance(cached, str):
            report.skipped(
                "image-conversion",
                f"{label} to SVG conversion failed; published as {label}",
                f"{preferred.as_posix()}: {cached}",
            )
            return None
        report.applied(
            "image-conversion", f"{label} to {'PNG' if cached.startswith(PNG_SIGNATURE) else 'SVG'}"
        )
        return cached

    def _store(self, results: dict[str, VectorResult]) -> None:
        for key, (result, findings) in results.items():
            self._converted[key] = result
            self._findings[key] = findings
