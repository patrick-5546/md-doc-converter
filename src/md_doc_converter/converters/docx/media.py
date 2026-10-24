"""Recover image descriptions and publish validated document media."""

from __future__ import annotations

import hashlib
import html
import re
import urllib.parse
import zipfile
from collections import Counter, defaultdict
from collections.abc import Collection
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
                pieces.append("\u2011")
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


def _shown_text(paragraph: ET.Element) -> str:
    """The text Word shows for *paragraph*, on one line."""
    pieces: list[str] = []
    for run in paragraph.iter(WORD + "r"):
        for node in run:
            if node.tag == WORD + "t":
                pieces.append(node.text or "")
            elif node.tag == WORD + "noBreakHyphen":
                pieces.append("\u2011")
            elif node.tag in {WORD + "tab", WORD + "br", WORD + "cr"}:
                pieces.append(" ")
    return " ".join("".join(pieces).split())


def image_descriptions(
    root: ET.Element,
    units: list[ET.Element] | None = None,
    headings: Collection[ET.Element] = (),
) -> list[ImageDescription]:
    """Describe each image by its figure caption, or else by Word's alt text, or else by the
    text of a heading in *headings* directly above it.

    A caption is the nearest paragraph with text above or below a paragraph with one image,
    in the same table cell or body. An image with a caption on both sides takes the one on
    the side where most of the document's captions are, and each caption describes one image.
    A heading describes the images of a paragraph without text when no paragraph with text
    or an image comes between them.
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

    def heading_above(paragraph: ET.Element | None) -> str | None:
        if paragraph is None or paragraph in headings or _ooxml.paragraph_text(paragraph):
            return None
        container = ancestor(paragraph, {WORD + "tc", WORD + "body"})
        for above in reversed(paragraphs[: paragraph_indices[paragraph]]):
            if _ooxml.paragraph_text(above) or counts[above]:
                if above in headings and ancestor(above, {WORD + "tc", WORD + "body"}) is container:
                    return _shown_text(above) or None
                return None
        return None

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
        heading = None if title or description else heading_above(paragraph)
        result.append(
            ImageDescription(
                title or description or heading,
                paragraph_indices[paragraph] if paragraph is not None else -1,
                from_caption=title is not None,
                from_heading=heading is not None,
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


def validate_svg(path: Path) -> None:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        raise ConversionError(f"Invalid SVG {path}: {error}") from error
    _validate_svg_root(root, path)


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
# exports the whole page; the SVG is cropped back to the drawing's frame.
Frame = tuple[float, float, float, float]
_LENGTH = re.compile(r"\s*([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*(mm|cm|in|pt|pc|px)?\s*")
_MM_PER_UNIT = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72, "pc": 25.4 / 6, "px": 25.4 / 96}
_ODF_DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
_ODF_FO = "{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}"
_ODF_STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
_ODF_SVG = "{urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0}"


def _length_mm(value: str | None) -> float | None:
    match = _LENGTH.fullmatch(value or "")
    return float(match.group(1)) * _MM_PER_UNIT[match.group(2) or "px"] if match else None


def _svg_frame(root: ET.Element) -> Frame | None:
    """The frame of the one drawing on a LibreOffice SVG page, in SVG user units."""
    boxes = [node for node in root.iter() if node.get("class") == "BoundingBox"]
    if len(boxes) != 1:
        return None
    try:
        x, y, width, height = (
            float(boxes[0].get(key, "")) for key in ("x", "y", "width", "height")
        )
    except ValueError:
        return None
    return x, y, width, height


def _odg_frame(path: Path) -> Frame | None:
    """The frame of the drawing on LibreOffice's page, as fractions of the page.

    The PDF export has no shape information, so an ODG export of the same page supplies it.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            content = ET.fromstring(archive.read("content.xml"))
            styles = ET.fromstring(archive.read("styles.xml"))
    except OSError, KeyError, zipfile.BadZipFile, ET.ParseError:
        return None
    page = next(content.iter(_ODF_DRAW + "page"), None)
    frames: list[ET.Element] = [] if page is None else page.findall(_ODF_DRAW + "frame")
    if page is None or len(frames) != 1:
        return None
    master = next(
        (
            node
            for node in styles.iter(_ODF_STYLE + "master-page")
            if node.get(_ODF_STYLE + "name") == page.get(_ODF_DRAW + "master-page-name")
        ),
        None,
    )
    layout = next(
        (
            node
            for node in styles.iter(_ODF_STYLE + "page-layout")
            if master is not None
            and node.get(_ODF_STYLE + "name") == master.get(_ODF_STYLE + "page-layout-name")
        ),
        None,
    )
    properties = None if layout is None else layout.find(_ODF_STYLE + "page-layout-properties")
    if properties is None:
        return None
    page_width = _length_mm(properties.get(_ODF_FO + "page-width"))
    page_height = _length_mm(properties.get(_ODF_FO + "page-height"))
    x, y, width, height = (
        _length_mm(frames[0].get(_ODF_SVG + key)) for key in ("x", "y", "width", "height")
    )
    if not page_width or not page_height or x is None or y is None or not width or not height:
        return None
    return x / page_width, y / page_height, width / page_width, height / page_height


def _crop_svg(path: Path, frame: Frame, *, fraction: bool = False) -> str | None:
    """Crop the SVG page at *path* to *frame*, given in user units or as fractions of the page;
    return why it could not be cropped."""
    try:
        # Its line endings are kept as LibreOffice wrote them.
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
    if fraction:
        x, y = left + x * page_width, top + y * page_height
        width, height = width * page_width, height * page_height
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
        raise ConversionError(f"PDF-to-SVG fallback SVG could not be read: {error}") from error
    start = re.search(r"<svg\b[^>]*>", text)
    if start is None:
        return
    tag = _UNITLESS_SIZE.sub(
        lambda size: f"{size[1]}{size[2]}{size[3]}{size[4]}pt{size[3]}", start.group(0)
    )
    path.write_text(text[: start.start()] + tag + text[start.end() :], encoding="utf-8", newline="")


# A converted drawing's SVG, or why it could not be converted, and notes on the conversion.
VectorResult = tuple[bytes | str, list[str]]
# The notes: LibreOffice's SVG export failed, and the PDF route is tried; or it worked, but the
# drawing could not be cut from LibreOffice's page.
_FALLBACK = "trying the PDF/pdftocairo vector fallback"
_WHOLE_PAGE = "kept LibreOffice's whole page around the drawing"


def _convert_vectors(drawings: dict[str, tuple[bytes, str]], work: Path) -> dict[str, VectorResult]:
    """Convert EMF/WMF drawings, by name, to SVGs cropped to each drawing.

    One LibreOffice run with one profile exports every drawing as SVG. The drawings it could not
    export take the PDF fallback together: one PDF run, one ODG run for their frames, and
    pdftocairo for each.
    """
    office = executables.libreoffice()
    if office is None:
        missing = "EMF conversion requires LibreOffice/soffice; other images do not"
        return {name: (missing, []) for name in drawings}
    sources = work / "sources"
    sources.mkdir(parents=True)
    paths: dict[str, Path] = {}
    for name, (data, suffix) in drawings.items():
        paths[name] = sources / f"{name}{suffix}"
        paths[name].write_bytes(data)
    profile = (work / "profile").as_uri()

    def export(
        kind: str, names: list[str], description: str
    ) -> tuple[Path, ConversionError | None]:
        """Export the drawings *names* in one run; return the folder and how the run failed.

        LibreOffice's warnings on success (such as a missing Java runtime) are not shown.
        """
        folder = work / kind
        folder.mkdir()
        command = [office, f"-env:UserInstallation={profile}", "--headless", "--convert-to", kind]
        try:
            _reference.run_command(
                [*command, "--outdir", str(folder), *(str(paths[name]) for name in names)],
                description,
                quiet=True,
            )
        except ConversionError as error:
            return folder, error
        return folder, None

    def produced(path: Path) -> bool:
        return path.is_file() and path.stat().st_size > 0

    findings: dict[str, list[str]] = {name: [] for name in drawings}
    results: dict[str, VectorResult] = {}
    svgs: dict[str, Path] = {}
    frames: dict[str, Frame | None] = {}
    fallback: dict[str, ConversionError] = {}
    folder, failure = export("svg", list(drawings), "EMF-to-SVG conversion")
    for name in drawings:
        svg = folder / f"{name}.svg"
        try:
            if not produced(svg):
                raise failure or ConversionError("LibreOffice SVG export produced no nonempty SVG")
            validate_svg(svg)
        except ConversionError as error:
            fallback[name] = error
            findings[name].append(f"{error}; {_FALLBACK}")
            continue
        svgs[name] = svg
        frames[name] = _svg_frame(ET.parse(svg).getroot())

    if fallback:
        cairo = executables.which("pdftocairo")
        pdfs, failure = (
            export("pdf", list(fallback), "EMF-to-PDF fallback conversion")
            if cairo is not None
            else (work / "pdf", None)
        )
        cairo_folder = work / "cairo"
        cairo_folder.mkdir()
        for name, direct_error in fallback.items():
            pdf, svg = pdfs / f"{name}.pdf", cairo_folder / f"{name}.svg"
            try:
                if cairo is None:
                    raise ConversionError(
                        f"{direct_error}; this EMF requires pdftocairo for PDF-to-SVG"
                    )
                if not produced(pdf):
                    raise failure or ConversionError(
                        f"{direct_error}; EMF fallback did not produce {pdf}"
                    )
                # Its warnings concern our intermediate PDF, which users cannot act on.
                _reference.run_command(
                    [cairo, "-svg", str(pdf), str(svg)],
                    "PDF-to-SVG fallback conversion",
                    quiet=True,
                )
                if not produced(svg):
                    raise ConversionError(f"{direct_error}; PDF-to-SVG fallback produced no SVG")
                validate_svg(svg)
                _size_in_points(svg)
            except ConversionError as error:
                results[name] = (str(error), findings[name])
                continue
            svgs[name] = svg
        located = [name for name in fallback if name in svgs]
        if located:
            odgs, _failure = export("odg", located, "EMF frame lookup")
            for name in located:
                frames[name] = _odg_frame(odgs / f"{name}.odg")

    for name, svg in svgs.items():
        fraction = name in fallback
        frame = frames[name]
        if frame is None:
            problem: str | None = (
                "LibreOffice did not report the drawing's frame"
                if fraction
                else "LibreOffice did not mark one drawing on it"
            )
        else:
            problem = _crop_svg(svg, frame, fraction=fraction)
        try:
            if problem is None:
                validate_svg(svg)
            else:
                findings[name].append(f"{_WHOLE_PAGE}: {problem}")
            results[name] = (svg.read_bytes(), findings[name])
        except (ConversionError, OSError) as error:
            results[name] = (str(error), findings[name])
    return results


def media_name(part: str, folder: str) -> PurePosixPath:
    """The name an image part is published under: relative to the media *folder* if in it."""
    name = PurePosixPath(part)
    return name.relative_to(folder) if name.is_relative_to(folder) else name


class ImageStager:
    """Publish each distinct image once under a unique name, converting EMF/WMF to SVG.

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
                content, preferred = converted, preferred.with_suffix(".svg")
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
            if message.endswith(_FALLBACK):
                # How the conversion turns out is reported next.
                report.warn(f"{preferred.as_posix()}: {message}", section="detail")
            else:
                report.warn(
                    f"{preferred.as_posix()}: {message}", kind=_WHOLE_PAGE, section="markdown"
                )
        if isinstance(cached, str):
            report.skipped(
                "image-conversion",
                f"{label} to SVG conversion failed; published as {label}",
                f"{preferred.as_posix()}: {cached}",
            )
            return None
        report.applied("image-conversion", f"{label} to SVG")
        return cached

    def _store(self, results: dict[str, VectorResult]) -> None:
        for key, (result, findings) in results.items():
            self._converted[key] = result
            self._findings[key] = findings
