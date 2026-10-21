"""Read DOCX packages safely and instrument private copies without editing the source."""

from __future__ import annotations

import copy
import dataclasses
import os
import posixpath
import stat
import urllib.parse
import uuid
import zipfile
import zlib
from collections.abc import Callable, Collection
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from md_doc_converter.converters.docx import explain as _explain
from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx import publication as _publication
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import tables as _tables
from md_doc_converter.converters.docx.diagnostics import TransformReport, describe, transform
from md_doc_converter.converters.docx.models import (
    BookmarkSection,
    ConversionError,
    ConversionPaths,
    PackageError,
    SourceDocument,
    SourceHeading,
    SourceImage,
    SourceListItem,
    SourceTable,
    WordPackage,
)
from md_doc_converter.converters.docx.ooxml import (
    DRAWING,
    DRAWING_WORD,
    IMAGE_CONTAINERS,
    IMAGE_NODES,
    OFFICE,
    PACKAGE_REL,
    REL,
    SVG_DRAWING,
    TEXT_BOX_CONTENT,
    VML,
    WORD,
    XML_SPACE,
)

DOCUMENT_PART = "word/document.xml"


RELATIONSHIPS_PART = "word/_rels/document.xml.rels"


STYLES_PART = "word/styles.xml"
NUMBERING_PART = "word/numbering.xml"


_TEXT_BOX_CONSEQUENCES = {
    "VML text box": "its text is kept after its paragraph",
    "DrawingML text box": "its text is omitted",
}


def _package_target(target: str) -> str:
    decoded = urllib.parse.unquote(target)
    if "\\" in decoded:
        raise ConversionError(f"Unsafe package media target: {target!r}")
    name = posixpath.normpath(posixpath.join("word", decoded))
    if name.startswith(("../", "/")) or name in {"", ".", ".."}:
        raise ConversionError(f"Package media target escapes the DOCX: {target!r}")
    return name


def _rebase_source_link(target: str, paths: ConversionPaths) -> str:
    parsed = _reference.split_uri(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return target
    target_path = paths.source_entry.parent / urllib.parse.unquote(parsed.path)
    if target_path.resolve() == paths.source or _publication.same_file(target_path, paths.source):
        return urllib.parse.urlunsplit(("", "", "", parsed.query, parsed.fragment))
    relative = os.path.relpath(target_path.resolve(), paths.output.parent)
    return urllib.parse.urlunsplit(
        (
            "",
            "",
            urllib.parse.quote(Path(relative).as_posix(), safe="/"),
            parsed.query,
            parsed.fragment,
        )
    )


def read_package(paths: ConversionPaths) -> WordPackage:
    """Read every part once; an unreadable or unsafe package is the only source error."""
    try:
        with zipfile.ZipFile(paths.source) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                raise PackageError("DOCX contains ambiguous duplicate package member names")
            for entry in entries:
                name = PurePosixPath(entry.filename)
                if entry.flag_bits & 1:
                    raise PackageError("Encrypted DOCX package members are not supported")
                if name.is_absolute() or ".." in name.parts or "\\" in entry.filename:
                    raise PackageError(f"Unsafe DOCX package member: {entry.filename!r}")
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise PackageError(f"Symlink in DOCX package: {entry.filename!r}")
            parts = {entry.filename: archive.read(entry) for entry in entries}
        root = ET.fromstring(parts[DOCUMENT_PART])
        relationships = (
            ET.fromstring(parts[RELATIONSHIPS_PART])
            if RELATIONSHIPS_PART in parts
            else ET.Element(PACKAGE_REL + "Relationships")
        )
        styles = (
            ET.fromstring(parts[STYLES_PART])
            if STYLES_PART in parts
            else ET.Element(WORD + "styles")
        )
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        ET.ParseError,
        KeyError,
        NotImplementedError,
        EOFError,
        zlib.error,
    ) as error:
        raise PackageError(
            f"Cannot read DOCX package {paths.source}; encrypted/protected containers "
            f"are not bypassed: {error}"
        ) from error
    return WordPackage(entries, parts, root, relationships, styles)


def _write_package(package: WordPackage, destination: Path, modified: dict[str, bytes]) -> None:
    pending = dict(modified)
    with zipfile.ZipFile(destination, "w") as archive:
        for entry in package.entries:
            archive.writestr(entry, pending.pop(entry.filename, package.parts[entry.filename]))
        for name, data in pending.items():
            archive.writestr(name, data)


def document_title(root: ET.Element) -> str | None:
    for paragraph in root.iter(WORD + "p"):
        style = paragraph.find(f"./{WORD}pPr/{WORD}pStyle")
        if style is not None and style.get(WORD + "val", "").casefold() == "title":
            title = _ooxml.paragraph_text(paragraph)
            if title:
                return title
    return None


def _image_part(node: ET.Element, relations: dict[str, ET.Element], parts: dict[str, bytes]) -> str:
    """The embedded package part an image node shows; the error says why there is none."""
    relation_id = node.get(REL + "embed") or node.get(REL + "id")
    if not relation_id:
        if node.get(REL + "link"):
            raise ConversionError("External/missing Word media is not fetched: linked image")
        raise ConversionError("Word image has no relationship")
    relation = relations.get(relation_id)
    if relation is None:
        raise ConversionError(f"Missing Word image relationship {relation_id!r}")
    if relation.get("TargetMode", "").casefold() == "external":
        raise ConversionError(
            f"External/missing Word media is not fetched: {relation.get('Target')!r}"
        )
    name = _package_target(relation.get("Target", ""))
    if name not in parts:
        raise ConversionError(f"Missing Word media part {name!r}")
    if not parts[name]:
        raise ConversionError(f"Empty Word media part {name!r}")
    return name


def _neutralize(node: ET.Element) -> None:
    """Leave an unreadable image out of both converters; MarkItDown stops on missing parts."""
    for attribute in (REL + "embed", REL + "link", REL + "id"):
        node.attrib.pop(attribute, None)


_MISSING_BOOKMARK = "link to a missing Word bookmark; kept as plain text"
_EMPTY_HEADING = "Word heading has no text; omitted"
_PICTURE_HEADING = "Word heading has only a picture; kept as a paragraph"
_PUNCTUATION_TRIMMED = "heading(s) with trailing punctuation trimmed"
_LEVELS_CLOSED = "heading(s) moved up to close a skipped level"
_TEXT_TAGS = frozenset(
    {WORD + "t", "{http://schemas.openxmlformats.org/officeDocument/2006/math}t"}
)


def _drop_missing_link_targets(
    root: ET.Element, relationships: ET.Element, report: TransformReport
) -> bool:
    """MarkItDown stops on a hyperlink whose relationship is missing; keep the link's text.

    mammoth raises a KeyError for it: https://github.com/mwilliamson/python-mammoth/issues/175
    """
    known = {node.get("Id", "") for node in relationships}
    changed = False
    for link in root.iter(WORD + "hyperlink"):
        identifier = link.get(REL + "id")
        if identifier is None or identifier in known:
            continue
        link.attrib.pop(REL + "id")
        changed = True
        report.skipped(
            "links",
            "Word hyperlink relationship is missing; its target is dropped",
            f"Word hyperlink relationship {identifier!r} is missing; its target is dropped",
        )
    return changed


def _map_symbols(root: ET.Element, report: TransformReport) -> bool:
    """Apply the symbols transform; return whether the document changed."""
    with transform(report, "symbols"):
        mapped, unknown = _ooxml.map_symbol_fonts(root)
        report.applied("symbols", count=mapped)
        for _character in range(unknown):
            report.skipped("symbols", "symbol-font character with no known Unicode symbol; kept")
        return bool(mapped or unknown)
    return False


def _drop_hidden_text(root: ET.Element, package: WordPackage, report: TransformReport) -> bool:
    """Apply the hidden-text transform; return whether the document changed."""
    with transform(report, "hidden-text"):
        dropped = _ooxml.drop_hidden_text(root, package.styles)
        report.applied("hidden-text", count=dropped)
        return bool(dropped)
    return False


def _plain_spaces(root: ET.Element, package: WordPackage, report: TransformReport) -> bool:
    """Apply the spaces transform; return whether the document changed."""
    with transform(report, "spaces"):
        count = _ooxml.plain_spaces(root, package.styles)
        report.applied("spaces", count=count)
        return bool(count)
    return False


def baseline_input(
    package: WordPackage, paths: ConversionPaths, work: Path, report: TransformReport
) -> Path:
    """MarkItDown's input without alignment: the source, minus what MarkItDown cannot read."""
    root = copy.deepcopy(package.root)
    relationships = copy.deepcopy(package.relationships)
    links = sum(1 for _link in root.iter(WORD + "hyperlink"))
    changed = False
    with transform(report, "fields"):
        # mammoth keeps the switches after a HYPERLINK field's URL, such as a \o tooltip, in
        # the link target (https://github.com/mwilliamson/python-mammoth/issues/174), so the
        # fields become links first, as for alignment.
        problems, pages = _ooxml.normalize_hyperlink_fields(root, relationships)
        for problem in problems:
            report.skipped(
                "links", "Word hyperlink field could not be decoded; kept as text", problem
            )
        report.applied("fields", "table-of-contents page number(s) dropped", pages)
        changed = bool(pages) or sum(1 for _link in root.iter(WORD + "hyperlink")) != links
    relations = {node.get("Id", ""): node for node in relationships}
    changed |= _drop_missing_link_targets(root, relationships, report)
    with transform(report, "fields"):
        expanded = _ooxml.expand_simple_fields(root)
        report.applied("fields", "simple field(s) kept", expanded)
        repaired = _ooxml.repair_fields(root)
        for detail, count in repaired.items():
            report.applied("fields", detail, count)
        changed |= bool(expanded or repaired)
    changed |= _map_symbols(root, report)
    changed |= _drop_hidden_text(root, package, report)
    changed |= _plain_spaces(root, package, report)
    for index, node in enumerate(node for node in root.iter() if node.tag in IMAGE_NODES):
        if node.tag == DRAWING + "blip" and node.get(REL + "embed") and node.get(REL + "link"):
            node.attrib.pop(REL + "link")
            changed = True
        try:
            _image_part(node, relations, package.parts)
        except ConversionError as error:
            _neutralize(node)
            changed = True
            report.skipped_error("images", error, f"image {index + 1}", "; image dropped")
    if not changed:
        return paths.source
    _write_package(
        package,
        work / "baseline.docx",
        {
            DOCUMENT_PART: _ooxml.word_xml_bytes(root, package.parts[DOCUMENT_PART]),
            RELATIONSHIPS_PART: ET.tostring(relationships, encoding="utf-8", xml_declaration=True),
        },
    )
    return work / "baseline.docx"


def prepare_source(
    package: WordPackage, paths: ConversionPaths, work: Path, report: TransformReport
) -> tuple[SourceDocument, Path]:
    """Instrument private copies for exact alignment; return the MarkItDown input."""
    root = copy.deepcopy(package.root)
    relationships = copy.deepcopy(package.relationships)
    _ooxml.accept_view(root)
    explainer = _explain.Explainer(package, report) if report.explaining else None
    if explainer is not None:
        explainer.count_fields(root)
    problems, pages = _ooxml.normalize_hyperlink_fields(root, relationships)
    for problem in problems:
        report.skipped("links", "Word hyperlink field could not be decoded; kept as text", problem)
    report.applied("fields", "table-of-contents page number(s) dropped", pages)
    _drop_missing_link_targets(root, relationships, report)
    bookmarks = {node.get(WORD + "name", "") for node in root.iter(WORD + "bookmarkStart")} - {""}
    for name in _ooxml.unlink_missing_references(root, bookmarks):
        report.skipped(
            "links",
            _MISSING_BOOKMARK,
            f"cross-reference to missing Word bookmark {name!r}; kept as plain text",
            shape=None
            if explainer is None
            else explainer.missing_bookmark(name, "cross-reference"),
        )
    with transform(report, "fields"):
        report.applied("fields", "simple field(s) kept", _ooxml.expand_simple_fields(root))
        for detail, count in _ooxml.repair_fields(root).items():
            report.applied("fields", detail, count)
    _map_symbols(root, report)
    _drop_hidden_text(root, package, report)
    _plain_spaces(root, package, report)
    _ooxml.split_drawing_runs(root)
    for problem in _tables.canonicalize_horizontal_merges(root, strict=False):
        report.warn(f"Legacy horizontal table merge kept as written: {problem}")
    _tables.complete_table_grids(root)
    with transform(report, "tables"):
        report.applied("tables", "empty row(s) omitted", _tables.drop_empty_rows(root))
    source = SourceDocument(root, "MDCONVDOCX" + uuid.uuid4().hex.upper())
    source.title = document_title(root)
    report.prefix = source.prefix
    numbered, numbering = _list_numbering(package, report)
    if explainer is not None:
        explainer.numbering = _explain.Numbering(numbering or package.parts.get(NUMBERING_PART))
    _instrument_source(source, package, relationships, paths, report, numbered, explainer)
    _keep_metadata_in_body(root, package)
    _text_boxes_after_paragraphs(root)
    _tables.pad_first_body_rows(root)
    modified = {
        DOCUMENT_PART: _ooxml.word_xml_bytes(root, package.parts[DOCUMENT_PART]),
        RELATIONSHIPS_PART: ET.tostring(relationships, encoding="utf-8", xml_declaration=True),
    }
    if numbering is not None:
        modified[NUMBERING_PART] = numbering
    if source.code_style is not None and STYLES_PART in package.parts:
        styles = copy.deepcopy(package.styles)
        styles.append(source.code_style)
        modified[STYLES_PART] = _ooxml.word_xml_bytes(styles, package.parts[STYLES_PART])
    _write_package(package, work / "reference.docx", modified)
    return source, _write_markitdown_copy(source, package, work, modified)


_PANDOC_METADATA_STYLES = frozenset({"title", "subtitle", "author", "date", "abstract"})


def _keep_metadata_in_body(root: ET.Element, package: WordPackage) -> None:
    """Keep leading Title-like paragraphs, and the markers in them, in Pandoc's body.

    Pandoc reads the leading paragraphs styled Title, Subtitle, Author, Date, or Abstract
    (with empty ones between) as metadata, which its HTML omits. Without the style they are
    ordinary paragraphs to Pandoc; MarkItDown has no style for them either.
    """
    styles = {node.get(WORD + "styleId", ""): node for node in package.styles}
    body = root.find(WORD + "body")
    for paragraph in [] if body is None else list(body):
        if paragraph.tag != WORD + "p" or _ooxml.pandoc_heading(paragraph, styles):
            return
        try:
            if _ooxml.numbering_reference(paragraph, styles) is not None:
                return
        except ConversionError:
            return
        properties = paragraph.find(WORD + "pPr")
        style = None if properties is None else properties.find(WORD + "pStyle")
        definition = None if style is None else styles.get(style.get(WORD + "val", ""))
        if properties is not None and style is not None and definition is not None:
            label = definition.find(WORD + "name")
            name = label.get(WORD + "val") if label is not None else None
            shown = name if name is not None else definition.get(WORD + "styleId", "")
            # Pandoc compares style names without case.
            if shown.casefold() in _PANDOC_METADATA_STYLES:
                properties.remove(style)
                continue
        blank = not _ooxml.paragraph_text(paragraph) and all(
            child.tag in {WORD + "pPr", WORD + "proofErr", WORD + "bookmarkEnd"}
            or (
                child.tag == WORD + "r"
                and all(
                    part.tag in {WORD + "rPr", WORD + "t", WORD + "tab", WORD + "br", WORD + "cr"}
                    for part in child
                )
            )
            for child in paragraph
        )
        if not blank:
            return


def _text_boxes_after_paragraphs(root: ET.Element) -> None:
    """Move VML text-box content after its paragraph, where MarkItDown puts it.

    Pandoc splits the paragraph around the text box instead, which a list item does not
    survive. Runs after alignment, so the moved text keeps no markers, as before.
    """
    parents = {child: parent for parent in root.iter() for child in parent}
    moved: dict[ET.Element, int] = {}
    for picture in list(_ooxml.scoped_iter(root, WORD + "pict")):
        paragraph = parents.get(picture)
        while paragraph is not None and paragraph.tag != WORD + "p":
            paragraph = parents.get(paragraph)
        container = None if paragraph is None else parents.get(paragraph)
        if paragraph is None or container is None:
            continue
        content: list[ET.Element] = []
        for box in list(picture.iter(TEXT_BOX_CONTENT)):
            owner = parents.get(box)
            if owner is None or any(
                node.tag == TEXT_BOX_CONTENT for node in _ancestors(box, parents, picture)
            ):
                continue
            content.extend(list(box))
            owner.remove(box)
        if not content:
            continue
        position = list(container).index(paragraph) + 1 + moved.get(paragraph, 0)
        container[position:position] = content
        moved[paragraph] = moved.get(paragraph, 0) + len(content)


def _ancestors(
    node: ET.Element, parents: dict[ET.Element, ET.Element], stop: ET.Element
) -> list[ET.Element]:
    """The ancestors of *node* below *stop*."""
    result: list[ET.Element] = []
    current = parents.get(node)
    while current is not None and current is not stop:
        result.append(current)
        current = parents.get(current)
    return result


def _list_numbering(
    package: WordPackage, report: TransformReport
) -> tuple[frozenset[str], bytes | None]:
    """Word's numbered lists, and the numbering part rewritten if list-style links changed it."""
    data = package.parts.get(NUMBERING_PART)
    if data is None:
        return frozenset(), None
    try:
        numbering = ET.fromstring(data)
    except ET.ParseError as error:
        report.failed("lists", ConversionError(f"Cannot read Word numbering: {error}"))
        return frozenset(), None
    styles = {node.get(WORD + "styleId", ""): node for node in package.styles}
    linked = _ooxml.link_numbering_styles(numbering, styles)
    return _ooxml.numbered_lists(numbering), (
        _ooxml.word_xml_bytes(numbering, data) if linked else None
    )


def _write_markitdown_copy(
    source: SourceDocument, package: WordPackage, work: Path, modified: dict[str, bytes]
) -> Path:
    """Label images for alignment and protect Word text from HTML/Markdown interpretation."""
    protected = copy.deepcopy(source.root)
    nodes = dict(zip(source.root.iter(), protected.iter(), strict=True))
    for element, attribute, value in source.alt_targets:
        nodes[element].set(attribute, value)
    # Mammoth reads w:object in place but moves w:pict content after its paragraph, outside
    # the markers; images go in a w:object and any text-box text stays in the w:pict.
    parents: dict[ET.Element, ET.Element] = {}
    for unit in source.pict_units:
        picture = nodes[unit]
        if next(picture.iter(TEXT_BOX_CONTENT), None) is None:
            picture.tag = WORD + "object"
            continue
        parents = parents or {child: parent for parent in protected.iter() for child in parent}
        images = copy.deepcopy(picture)
        images.tag = WORD + "object"
        _detach(images, list(images.iter(TEXT_BOX_CONTENT)))
        _detach(picture, _ooxml.unit_image_nodes(picture))
        owner = parents[picture]
        owner.insert(list(owner).index(picture), images)
    # MarkItDown escapes only * and _; these would otherwise become HTML, code spans, escape
    # sequences, headings, or links, such as "bit[3](W1C)":
    # https://github.com/microsoft/markitdown/issues/2157#issuecomment-6027162097
    literals = (
        ("<", "LESS"),
        (">", "GREATER"),
        ("&", "AMPERSAND"),
        ("`", "BACKTICK"),
        ("\\", "BACKSLASH"),
        ("#", "HASH"),
        ("[", "OPENBRACKET"),
        ("]", "CLOSEBRACKET"),
    )
    for node in protected.iter(WORD + "t"):
        if node.text is None:
            continue
        for character, name in literals:
            if character in node.text:
                token = source.prefix + "LITERAL" + name
                source.literal_tokens[token] = character
                source.literal_count += node.text.count(character)
                node.text = node.text.replace(character, token)
    # MarkItDown writes a line break as two trailing spaces, then strips them
    # (https://github.com/microsoft/markitdown/issues/2642; proposed fix:
    # https://github.com/microsoft/markitdown/pull/2643). Mark each one; restore_literal_text
    # starts a new paragraph there, or writes a space where Markdown has no line break.
    breaking = source.prefix + "LITERALBREAK"
    for run in protected.iter(WORD + "r"):
        for position, child in enumerate(list(run)):
            kind = child.get(WORD + "type", "textWrapping")
            if child.tag == WORD + "cr" or (child.tag == WORD + "br" and kind == "textWrapping"):
                replacement = ET.Element(WORD + "t", {XML_SPACE: "preserve"})
                replacement.text = breaking
                run[position] = replacement
                source.literal_tokens[breaking] = " "
                source.line_breaks += 1
    if not source.alt_targets and not source.literal_tokens and not source.pict_units:
        return work / "reference.docx"
    document = _ooxml.word_xml_bytes(protected, package.parts[DOCUMENT_PART])
    _write_package(package, work / "markitdown.docx", {**modified, DOCUMENT_PART: document})
    return work / "markitdown.docx"


def _detach(root: ET.Element, targets: list[ET.Element]) -> None:
    parents = {child: parent for parent in root.iter() for child in parent}
    for target in targets:
        parents[target].remove(target)


def _shows_text(paragraph: ET.Element) -> bool:
    """Whether the paragraph shows text, other than that of the text boxes anchored to it."""
    return any(
        node.tag == WORD + "sym" or (node.tag in _TEXT_TAGS and (node.text or "").strip())
        for node in _ooxml.scoped_iter(paragraph)
    )


def _has_picture(paragraph: ET.Element) -> bool:
    return any(node.tag in IMAGE_CONTAINERS for node in _ooxml.scoped_iter(paragraph))


def _make_plain_paragraph(paragraph: ET.Element) -> None:
    """Drop the paragraph's style, outline level, and numbering, so it is a plain paragraph."""
    properties = paragraph.find(WORD + "pPr")
    if properties is None:
        return
    for name in ("pStyle", "outlineLvl", "numPr"):
        for node in properties.findall(WORD + name):
            properties.remove(node)


_VISIBLE_TAGS = frozenset({WORD + "tbl", WORD + "sym", *IMAGE_CONTAINERS})


def _following_heading(
    start: ET.Element,
    nodes: list[ET.Element],
    positions: dict[ET.Element, int],
    headings: dict[ET.Element, SourceHeading],
) -> SourceHeading | None:
    """The body heading after *start*, if nothing visible comes between them."""
    for index in range(positions[start] + 1, len(nodes)):
        node = nodes[index]
        heading = headings.get(node)
        if heading is not None:
            return None if heading.in_table else heading
        if node.tag in _VISIBLE_TAGS or (node.tag in _TEXT_TAGS and (node.text or "").strip()):
            return None
    return None


def _word_bookmark_sections(
    root: ET.Element,
    parents: dict[ET.Element, ET.Element],
    headings: dict[ET.Element, SourceHeading],
) -> dict[str, BookmarkSection]:
    nodes = list(root.iter())
    positions = {node: index for index, node in enumerate(nodes)}
    current: str | None = None
    result: dict[str, BookmarkSection] = {}
    for node in nodes:
        heading = headings.get(node)
        if heading is not None and not heading.in_table:
            current = heading.token
        if node.tag != WORD + "bookmarkStart":
            continue
        name = node.get(WORD + "name", "")
        if not name or name in result:
            continue
        owner = parents.get(node)
        paragraph: ET.Element | None = None
        table = False
        while owner is not None:
            if owner.tag == WORD + "p" and paragraph is None:
                paragraph = owner
            table |= owner.tag == WORD + "tbl"
            owner = parents.get(owner)
        direct = headings.get(paragraph) if paragraph is not None else None
        if direct is not None and direct.in_table:
            direct = None
        if direct is None and not table:
            # A bookmark with nothing visible before the next heading is at that heading, as
            # when Enter at the start of a heading leaves its table-of-contents bookmark in
            # the empty paragraph above it.
            direct = _following_heading(node, nodes, positions, headings)
        result[name] = BookmarkSection(
            direct.token if direct is not None else current, direct is not None
        )
    return result


def _select_image(
    node: ET.Element,
    relations: dict[str, ET.Element],
    parents: dict[ET.Element, ET.Element],
    package: WordPackage,
    report: TransformReport,
    where: str,
) -> str:
    svg = next(node.iter(SVG_DRAWING + "svgBlip"), None) if node.tag == DRAWING + "blip" else None
    if svg is not None:
        try:
            name = _image_part(svg, relations, package.parts)
            _media.validate_svg_bytes(package.parts[name], name)
        except ConversionError as error:
            report.skipped(
                "checks",
                "Word SVG version is unusable; its fallback image is used",
                f"{where}: {describe(error)}; its fallback image is used",
            )
            owner = parents.get(svg)
            if owner is not None:
                owner.remove(svg)
        else:
            node.set(REL + "embed", svg.get(REL + "embed", ""))
            node.attrib.pop(REL + "link", None)
            return name
    name = _image_part(node, relations, package.parts)
    if node.get(REL + "embed"):
        node.attrib.pop(REL + "link", None)
    return name


def _instrument_images(
    source: SourceDocument,
    package: WordPackage,
    relations: dict[str, ET.Element],
    paragraphs: list[ET.Element],
    parents: dict[ET.Element, ET.Element],
    surround: Callable[[ET.Element, str], None],
    report: TransformReport,
    explainer: _explain.Explainer | None = None,
    headings: Collection[ET.Element] = (),
) -> None:
    units = _ooxml.image_units(source.root)
    descriptions = _media.image_descriptions(source.root, units, headings)
    for index, (unit, description) in enumerate(zip(units, descriptions, strict=True)):
        where = f"image at paragraph {description.paragraph_index + 1}"
        token = source.prefix + f"I{index}"
        if explainer is not None:
            explainer.image(token, unit, description.alt_text)
        found: list[SourceImage] = []
        for node in _ooxml.unit_image_nodes(unit):
            try:
                name = _select_image(node, relations, parents, package, report, where)
            except ConversionError as error:
                _neutralize(node)
                report.skipped_error("images", error, where, "; image dropped", items=(token,))
                continue
            source.media[name] = package.parts[name]
            relative = PurePosixPath(name)
            if name.startswith("word/media/"):
                relative = relative.relative_to("word/media")
            found.append(SourceImage(token, name, relative, description))
        if not found:
            continue
        try:
            surround(unit, token)
        except ConversionError as error:
            report.skipped_error(
                "images", error, where, "; kept where MarkItDown puts it", items=(token,)
            )
            continue
        source.images.extend(found)
        if unit.tag == WORD + "pict":
            source.pict_units.append(unit)
        properties = next(_ooxml.scoped_iter(unit, DRAWING_WORD + "docPr"), None)
        if properties is not None:
            source.alt_targets.append((properties, "descr", token + "ALT"))
        for node in _ooxml.scoped_iter(unit, VML + "imagedata"):
            source.alt_targets.append((node, OFFICE + "title", token + "ALT"))
    numbers = {paragraph: index for index, paragraph in enumerate(paragraphs)}
    for container in _ooxml.image_containers_without_images(source.root):
        kind = _ooxml.container_kind(container)
        consequence = _TEXT_BOX_CONSEQUENCES.get(kind, "omitted")
        owner: ET.Element | None = container
        while owner is not None and owner.tag != WORD + "p":
            owner = parents.get(owner)
        where = f"paragraph {numbers[owner] + 1}" if owner in numbers else "the document"
        report.skipped(
            "images",
            f"{kind}: no embedded image; {consequence}",
            f"{kind} at {where}: no embedded image; {consequence}",
            shape=None if explainer is None else explainer.container_shape(container),
        )


def _close_heading_gaps(
    source: SourceDocument,
    headings: dict[ET.Element, SourceHeading],
    list_item: Callable[[ET.Element], bool],
    report: TransformReport,
) -> None:
    """Move each heading up to one level below the heading it belongs to, with its subheadings.

    markdownlint's MD001 reports a heading more than one level below the one before it, as
    when a Heading 4 follows a Heading 1. A heading with no lower-level heading before it
    keeps its level, so a document may still start below level 1. Headings in tables, and
    numbered headings kept as list items, are not Markdown headings and are left out.
    Heading IDs do not depend on levels.
    """
    open_levels: list[tuple[int, int]] = []
    for paragraph, heading in list(headings.items()):
        if heading.in_table or list_item(paragraph):
            continue
        while open_levels and open_levels[-1][0] >= heading.level:
            open_levels.pop()
        level = open_levels[-1][1] + 1 if open_levels else heading.level
        open_levels.append((heading.level, level))
        if level == heading.level:
            continue
        moved = dataclasses.replace(heading, level=level)
        headings[paragraph] = moved
        source.headings[source.headings.index(heading)] = moved
        report.applied("headings", _LEVELS_CLOSED)


_CODE_STYLE_NAME = "Verbatim Char"
# MarkItDown's mammoth writes a run in this character style as inline code, as Pandoc does.
CODE_STYLE_MAP = f"r[style-name='{_CODE_STYLE_NAME}'] => code"
# A paragraph holding any of these is not a code line: they are recovered on their own.
_NOT_CODE = frozenset(
    {
        WORD + "hyperlink",
        WORD + "fldChar",
        WORD + "fldSimple",
        WORD + "instrText",
        *IMAGE_CONTAINERS,
        "{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath",
    }
)
# Elements between runs that neither end a run of code nor show anything.
_TRANSPARENT = frozenset(
    {WORD + "proofErr", WORD + "bookmarkStart", WORD + "bookmarkEnd", WORD + "permStart"}
)
_RUN_WRAPPERS = frozenset(
    {WORD + "smartTag", WORD + "customXml", WORD + "sdt", WORD + "sdtContent"}
)


def _code_style(package: WordPackage) -> tuple[str, ET.Element | None]:
    """The id of a Verbatim Char character style, and a definition when the document has none."""
    for style in package.styles.findall(WORD + "style"):
        name = style.find(WORD + "name")
        if (
            style.get(WORD + "type") == "character"
            and name is not None
            and name.get(WORD + "val") == _CODE_STYLE_NAME
        ):
            return style.get(WORD + "styleId", ""), None
    taken = {style.get(WORD + "styleId", "") for style in package.styles.findall(WORD + "style")}
    identity = next(
        f"VerbatimChar{suffix}"
        for suffix in ("", *map(str, range(1, 99)))
        if f"VerbatimChar{suffix}" not in taken
    )
    definition = ET.Element(
        WORD + "style", {WORD + "type": "character", WORD + "styleId": identity}
    )
    ET.SubElement(definition, WORD + "name", {WORD + "val": _CODE_STYLE_NAME})
    return identity, definition


def _split_code_edges(run: ET.Element, parent: ET.Element) -> None:
    """Move the spaces and tabs that start or end *run* into plain runs beside it."""
    content = [child for child in run if child.tag != WORD + "rPr"]
    for edge in (0, -1):
        pieces: list[ET.Element] = []
        while content:
            child = content[edge]
            if child.tag in {WORD + "tab", WORD + "ptab"}:
                pieces.append(child)
                run.remove(child)
                content.remove(child)
                continue
            if child.tag == WORD + "t":
                text = child.text or ""
                kept = text.lstrip() if edge == 0 else text.rstrip()
                if kept:
                    if kept != text:
                        moved = ET.Element(WORD + "t", {XML_SPACE: "preserve"})
                        moved.text = (
                            text[: len(text) - len(kept)] if edge == 0 else text[len(kept) :]
                        )
                        child.text = kept
                        pieces.append(moved)
                    break
                pieces.append(child)
                run.remove(child)
                content.remove(child)
                continue
            break
        if not pieces:
            continue
        outer = ET.Element(WORD + "r")
        outer.extend(pieces if edge == 0 else reversed(pieces))
        position = list(parent).index(run)
        parent.insert(position if edge == 0 else position + 1, outer)


def _split_at_breaks(run: ET.Element, parent: ET.Element) -> None:
    """Give each line break in *run* a run of its own, so that no code span crosses one."""
    properties = run.find(WORD + "rPr")
    pieces: list[list[ET.Element]] = [[]]
    for child in run:
        if child.tag in {WORD + "br", WORD + "cr"}:
            pieces.extend([[child], []])
        elif child.tag != WORD + "rPr":
            pieces[-1].append(child)
    position = list(parent).index(run)
    parent.remove(run)
    for piece in filter(None, pieces):
        split = ET.Element(run.tag, run.attrib)
        if properties is not None:
            split.append(copy.deepcopy(properties))
        split.extend(piece)
        parent.insert(position, split)
        position += 1


def _code_spans(
    paragraph: ET.Element, properties: _ooxml.RunProperties
) -> list[tuple[ET.Element, list[ET.Element]]]:
    """Each run of consecutive monospace runs with visible text outside fields, with the
    element that holds them."""
    spans: list[tuple[ET.Element, list[ET.Element]]] = []
    depth = 0

    def collect(container: ET.Element) -> None:
        nonlocal depth
        current: list[ET.Element] = []

        def close() -> None:
            nonlocal current
            if any(
                (node.text or "").strip() for run in current for node in run.findall(WORD + "t")
            ):
                spans.append((container, current))
            current = []

        for child in list(container):
            if (
                child.tag == WORD + "r"
                and (child.find(WORD + "br") is not None or child.find(WORD + "cr") is not None)
                and properties.monospace(child.find(WORD + "rPr"), paragraph)
            ):
                _split_at_breaks(child, container)
        for child in list(container):
            if child.tag in _TRANSPARENT:
                continue
            if child.tag in _RUN_WRAPPERS:
                close()
                collect(child)
                continue
            if child.tag != WORD + "r":
                close()
                continue
            marks = [node.get(WORD + "fldCharType") for node in child.iter(WORD + "fldChar")]
            code = (
                not depth
                and not marks
                and any(node.tag == WORD + "t" for node in child)
                and properties.monospace(child.find(WORD + "rPr"), paragraph)
            )
            depth += marks.count("begin") - marks.count("end")
            if code:
                current.append(child)
            else:
                close()
        close()

    collect(paragraph)
    return spans


def _instrument_code(
    source: SourceDocument,
    package: WordPackage,
    styles: dict[str, ET.Element],
    list_item: Callable[[ET.Element], bool],
    report: TransformReport,
) -> None:
    """Mark text in a monospace font as code, before the tables are copied for the checks.

    Consecutive body paragraphs whose text is all monospace, with the empty paragraphs
    between them, become one code block between markers, which recovery replaces with
    their Word text; so does a top-level one-cell table that holds only such paragraphs.
    Other monospace runs, outside headings, hyperlinks, and fields, take the Verbatim Char
    style, which both converters write as inline code. Word's typographic quotes, dashes,
    and ellipses in code become ASCII.
    """
    root = source.root
    body = root.find(WORD + "body")
    if body is None:
        return
    properties = _ooxml.RunProperties(package.styles)

    def heading(paragraph: ET.Element) -> bool:
        try:
            return _ooxml.heading_level(paragraph, styles) is not None
        except ConversionError:
            return True

    def plain(paragraph: ET.Element) -> bool:
        return (
            not heading(paragraph)
            and not list_item(paragraph)
            and not any(node.tag in _NOT_CODE for node in _ooxml.scoped_iter(paragraph))
        )

    def code_paragraph(paragraph: ET.Element) -> bool:
        runs = [
            run
            for run in _ooxml.scoped_iter(paragraph, WORD + "r")
            if any((node.text or "").strip() for node in run.findall(WORD + "t"))
        ]
        return (
            bool(runs)
            and plain(paragraph)
            and all(properties.monospace(run.find(WORD + "rPr"), paragraph) for run in runs)
        )

    def empty(paragraph: ET.Element) -> bool:
        return not _ooxml.paragraph_text(paragraph) and plain(paragraph)

    blank_lines: set[ET.Element] = set()
    for table in [child for child in body if child.tag == WORD + "tbl"]:
        rows = table.findall(WORD + "tr")
        cells: list[ET.Element] = rows[0].findall(WORD + "tc") if len(rows) == 1 else []
        content: list[ET.Element] = (
            [node for node in cells[0] if node.tag != WORD + "tcPr"] if len(cells) == 1 else []
        )
        if (
            not content
            or any(node.tag != WORD + "p" for node in content)
            or not any(code_paragraph(node) for node in content)
            or not all(code_paragraph(node) or empty(node) for node in content)
        ):
            continue
        position = list(body).index(table)
        body.remove(table)
        for offset, node in enumerate(content):
            body.insert(position + offset, node)
        blank_lines.update(node for node in content if empty(node))
        report.applied("code", "one-cell table(s) made code blocks")

    groups: list[list[ET.Element]] = []
    current: list[ET.Element] = []
    pending: list[ET.Element] = []
    for node in list(body):
        if node.tag in _TRANSPARENT:
            continue
        if node.tag == WORD + "p" and code_paragraph(node):
            current.extend([*pending, node])
            pending = []
            continue
        if (
            current
            and node.tag == WORD + "p"
            and empty(node)
            and (
                node in blank_lines
                or properties.monospace(node.find(f"./{WORD}pPr/{WORD}rPr"), node)
            )
        ):
            pending.append(node)
            continue
        if current:
            groups.append(current)
        current = []
        pending = []
    if current:
        groups.append(current)
    for group in groups:
        token = source.prefix + f"C{len(source.code_blocks)}"
        lines: list[str] = []
        for paragraph in group:
            text, straightened = _ooxml.straighten_code(_ooxml.code_text(paragraph))
            report.applied("code", "curly quote(s) and dash(es) straightened", straightened)
            lines.extend(line.rstrip() for line in text.split("\n"))
        # A line break that starts or ends the code leaves an empty line, which Word shows as
        # space around the code.
        source.code_blocks[token] = "\n".join(lines).strip("\n")
        body.insert(list(body).index(group[0]), _ooxml.word_paragraph(token + "BEGIN"))
        body.insert(list(body).index(group[-1]) + 1, _ooxml.word_paragraph(token + "END"))

    in_blocks = {paragraph for group in groups for paragraph in group}
    identity: str | None = None
    for paragraph in list(_ooxml.scoped_iter(root, WORD + "p")):
        if paragraph in in_blocks or heading(paragraph):
            continue
        for container, runs in _code_spans(paragraph, properties):
            first = runs[0]
            for run in runs[1:]:
                first.extend([child for child in run if child.tag != WORD + "rPr"])
                container.remove(run)
            _split_code_edges(first, container)
            for node in first.findall(WORD + "t"):
                node.text, straightened = _ooxml.straighten_code(node.text or "")
                report.applied("code", "curly quote(s) and dash(es) straightened", straightened)
            if identity is None:
                identity, source.code_style = _code_style(package)
            run_properties = first.find(WORD + "rPr")
            if run_properties is None:
                run_properties = ET.Element(WORD + "rPr")
                first.insert(0, run_properties)
            for old in run_properties.findall(WORD + "rStyle"):
                run_properties.remove(old)
            run_properties.insert(0, ET.Element(WORD + "rStyle", {WORD + "val": identity}))
            report.applied("code", "inline code span(s)")


def _instrument_source(
    source: SourceDocument,
    package: WordPackage,
    relationships: ET.Element,
    paths: ConversionPaths,
    report: TransformReport,
    numbered: frozenset[str],
    explainer: _explain.Explainer | None = None,
) -> None:
    root = source.root
    prefix = source.prefix
    parents = {child: parent for parent in root.iter() for child in parent}
    styles = {node.get(WORD + "styleId", ""): node for node in package.styles}
    if explainer is not None:
        explainer.start(root, parents, relationships)

    def in_table(node: ET.Element) -> bool:
        current = parents.get(node)
        while current is not None:
            if current.tag == WORD + "tbl":
                return True
            current = parents.get(current)
        return False

    def surround(node: ET.Element, token: str) -> None:
        parent = parents.get(node)
        if parent is not None and parent.tag == WORD + "r":
            node = parent
            parent = parents.get(node)
        if parent is None:
            raise ConversionError("Source object has no container")
        position = list(parent).index(node)
        paragraph_container = parent.tag in {WORD + "body", WORD + "tc"}

        def make_marker(text: str) -> ET.Element:
            if paragraph_container:
                return _ooxml.word_paragraph(text)
            if parent.tag == WORD + "r":
                element = ET.Element(WORD + "t", {XML_SPACE: "preserve"})
                element.text = text
                return element
            return _ooxml.word_run(text)

        parent.insert(position, make_marker(token + "BEGIN"))
        parent.insert(position + 2, make_marker(token + "END"))

    def mark(paragraph: ET.Element, token: str, *, closed: bool) -> None:
        position = 1 if paragraph.find(WORD + "pPr") is not None else 0
        paragraph.insert(position, _ooxml.word_run(token + ("BEGIN" if closed else "")))
        if closed:
            paragraph.append(_ooxml.word_run(token + "END"))

    def list_item(paragraph: ET.Element) -> bool:
        """Whether *paragraph* is an item of a Word list, as Pandoc reads it."""
        if _ooxml.pandoc_heading(paragraph, styles):
            return False
        try:
            reference = _ooxml.numbering_reference(paragraph, styles)
        except ConversionError:
            return False
        return reference is not None and int(reference) != 0 and reference in numbered

    # Before the tables are copied for the final checks, which compare their text.
    for paragraph in _ooxml.scoped_iter(root, WORD + "p"):
        try:
            level = _ooxml.heading_level(paragraph, styles)
        except ConversionError:
            continue
        # A numbered heading by outline level stays a list item, which keeps its text.
        if level is None or (not in_table(paragraph) and list_item(paragraph)):
            continue
        if _ooxml.trim_trailing_punctuation(paragraph):
            report.applied("headings", _PUNCTUATION_TRIMMED)
    with transform(report, "code"):
        _instrument_code(source, package, styles, list_item, report)
    # Code blocks move paragraphs out of one-cell tables, and inline code merges runs.
    parents.clear()
    parents.update({child: parent for parent in root.iter() for child in parent})

    tables = list(_ooxml.scoped_iter(root, WORD + "tbl"))
    for index, table in enumerate(tables):
        copied = copy.deepcopy(table)
        items = frozenset(node for node in copied.iter(WORD + "p") if list_item(node))
        source.tables.append(SourceTable(prefix + f"T{index}", copied, in_table(table), items))
        if explainer is not None:
            explainer.table(prefix + f"T{index}", table, in_table(table))
    paragraphs = list(_ooxml.scoped_iter(root, WORD + "p"))
    heading_lookup: dict[ET.Element, SourceHeading] = {}
    for index, paragraph in enumerate(paragraphs):
        try:
            level = _ooxml.heading_level(paragraph, styles)
        except ConversionError as error:
            reason = (
                "Word heading levels 7-9 have no Markdown equivalent"
                if "heading level" in str(error)
                else "Word heading style could not be resolved"
            )
            report.skipped(
                "headings",
                reason + "; kept as a paragraph",
                f"paragraph {index + 1}: {error}; kept as a paragraph",
                shape=None if explainer is None else explainer.heading_shape(paragraph),
            )
            continue
        if level is not None and not _shows_text(paragraph):
            # Neither converter may make it a heading: an empty one has no ID to link to,
            # and one with only a picture takes its ID from the picture's alt text in Pandoc
            # but not in GFM.
            picture = _has_picture(paragraph)
            reason = _PICTURE_HEADING if picture else _EMPTY_HEADING
            report.skipped(
                "headings",
                reason,
                f"paragraph {index + 1}: {reason}",
                shape=None if explainer is None else explainer.heading_shape(paragraph),
            )
            _make_plain_paragraph(paragraph)
            continue
        if level is not None:
            heading = SourceHeading(
                prefix + f"H{index}",
                level,
                in_table(paragraph),
                _ooxml.paragraph_text(paragraph),
            )
            heading_lookup[paragraph] = heading
            source.headings.append(heading)
            if explainer is not None:
                explainer.heading(heading.token, paragraph, level)
    _close_heading_gaps(source, heading_lookup, list_item, report)
    source.bookmark_sections = _word_bookmark_sections(root, parents, heading_lookup)
    relations = {node.get("Id", ""): node for node in relationships}
    _instrument_images(
        source,
        package,
        relations,
        paragraphs,
        parents,
        surround,
        report,
        explainer,
        headings=heading_lookup.keys(),
    )
    for relation in relationships:
        if relation.get("Type", "").endswith("/hyperlink"):
            try:
                relation.set("Target", _rebase_source_link(relation.get("Target", ""), paths))
            except ConversionError as error:
                report.skipped_error("links", error, "hyperlink target", "; target kept as written")
    scope = set(_ooxml.scoped_iter(root))
    seen: set[str] = set()
    for bookmark in list(root.iter(WORD + "bookmarkStart")):
        parent = parents[bookmark]
        position = list(parent).index(bookmark)
        parent.remove(bookmark)
        name = bookmark.get(WORD + "name", "")
        if bookmark not in scope:
            continue
        if not name or name in seen:
            reason = (
                "Word bookmark has no name; dropped"
                if not name
                else "duplicate Word bookmark name; the later one is dropped"
            )
            report.skipped(
                "bookmarks",
                reason,
                f"{reason}: {name!r}" if name else reason,
                shape={"name": _explain.name_class(name)},
            )
            continue
        seen.add(name)
        if parent.tag in {WORD + "tbl", WORD + "tr"}:
            continue
        token = prefix + f"A{len(source.anchors)}ANCHOR"
        source.anchors[token] = name
        marker = (
            _ooxml.word_paragraph(token)
            if parent.tag in {WORD + "body", WORD + "tc"}
            else _ooxml.word_run(token)
        )
        parent.insert(position, marker)
        if explainer is not None:
            explainer.bookmark(token, marker, parent, name)
    for node in list(root.iter(WORD + "bookmarkEnd")):
        parents[node].remove(node)
    for link in list(root.iter(WORD + "hyperlink")):
        anchor = link.get(WORD + "anchor")
        relation = relations.get(link.get(REL + "id") or "")
        target = relation.get("Target", "") if relation is not None else ""
        try:
            parsed = _reference.split_uri(target)
        except ConversionError:
            continue
        if anchor and relation is not None and (parsed.path or parsed.scheme or parsed.netloc):
            new_id = prefix + "LINK" + str(len(relationships))
            new_relation = copy.deepcopy(relation)
            new_relation.set("Id", new_id)
            new_relation.set(
                "Target",
                urllib.parse.urlunsplit(
                    (
                        parsed.scheme,
                        parsed.netloc,
                        parsed.path,
                        parsed.query,
                        urllib.parse.quote(anchor, safe=""),
                    )
                ),
            )
            relationships.append(new_relation)
            link.set(REL + "id", new_id)
            link.attrib.pop(WORD + "anchor")
            continue
        local_anchor = anchor or (
            urllib.parse.unquote(parsed.fragment)
            if not parsed.path and not parsed.scheme and not parsed.netloc
            else ""
        )
        if local_anchor and local_anchor not in source.bookmark_sections:
            parent = parents[link]
            position = list(parent).index(link)
            children = list(link)
            parent[position : position + 1] = children
            for child in children:
                parents[child] = parent
            report.skipped(
                "links",
                _MISSING_BOOKMARK,
                f"link to missing Word bookmark {local_anchor!r}; kept as plain text",
                shape=None
                if explainer is None
                else explainer.missing_bookmark(local_anchor, "link"),
            )

    def renders_link(link: ET.Element) -> bool:
        """Neither converter makes a link without a target or without visible content."""
        return bool(link.get(REL + "id") or link.get(WORD + "anchor")) and (
            bool(_ooxml.word_text(link).strip())
            or any(node.tag in IMAGE_NODES for node in link.iter())
        )

    for link in list(_ooxml.scoped_iter(root, WORD + "hyperlink")):
        if not renders_link(link):
            continue
        outer = parents.get(link)
        while outer is not None and not (outer.tag == WORD + "hyperlink" and renders_link(outer)):
            outer = parents.get(outer)
        if outer is not None:
            report.skipped(
                "links",
                "Word hyperlink nested in another hyperlink; its text stays in the outer link",
                "hyperlink nested in another hyperlink; its text stays in the outer link",
            )
            continue
        token = prefix + f"L{len(source.links)}"
        source.links.append(token)
        if explainer is not None:
            explainer.link(token, link)
        surround(link, token)
    for index, paragraph in enumerate(paragraphs):
        heading = heading_lookup.get(paragraph)
        if heading is not None:
            mark(paragraph, heading.token, closed=True)
        elif in_table(paragraph):
            token = prefix + f"P{index}PARAGRAPH"
            source.paragraph_tokens.append(token)
            mark(paragraph, token, closed=False)
        if (heading is not None and heading.in_table) or _ooxml.pandoc_heading(paragraph, styles):
            continue
        try:
            reference = _ooxml.numbering_reference(paragraph, styles)
        except ConversionError as error:
            if heading is None:
                report.skipped_error(
                    "lists", error, f"paragraph {index + 1}", "; kept as MarkItDown produced it"
                )
            continue
        if reference is None:
            continue
        listed = int(reference) != 0 and reference in numbered
        tabled = in_table(paragraph)
        if heading is not None:
            # A numbered heading by outline level, not by a "heading N" style, is a list item to
            # Pandoc and MarkItDown; mark it so that its list's region covers it.
            if listed:
                token = prefix + f"N{index}"
                if explainer is not None:
                    explainer.list_item(token, paragraph, index, continuation=False, heading=True)
                source.list_items.append(SourceListItem(token, tabled))
                mark(paragraph, token, closed=True)
                source.listed_headings.add(heading.token)
            continue
        # Pandoc adds a numbered paragraph that is not a list item, such as one with numId 0,
        # to the list item before it; mark it so that list's region covers it.
        continuation = not (listed or tabled) and (
            bool(_ooxml.paragraph_text(paragraph))
            or any(node.tag in IMAGE_CONTAINERS for node in paragraph.iter())
        )
        if listed or continuation:
            token = prefix + f"N{index}"
            if explainer is not None:
                explainer.list_item(
                    token, paragraph, index, continuation=continuation, heading=False
                )
            source.list_items.append(SourceListItem(token, tabled, continuation))
            mark(paragraph, token, closed=True)
    for table in tables:
        width = max(1, len(table.findall(f"./{WORD}tblGrid/{WORD}gridCol")))
        for row in table.findall(WORD + "tr"):
            if row.findall(WORD + "tc"):
                continue
            token = prefix + f"E{len(source.empty_row_tokens)}EMPTYROW"
            source.empty_row_tokens.append(token)
            cell = ET.SubElement(row, WORD + "tc")
            properties = ET.SubElement(cell, WORD + "tcPr")
            ET.SubElement(properties, WORD + "gridSpan", {WORD + "val": str(width)})
            cell.append(_ooxml.word_paragraph(token))
    for table, descriptor in zip(tables, source.tables, strict=True):
        surround(table, descriptor.token)
