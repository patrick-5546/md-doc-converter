"""Prepare a private copy of a Word document so that Pandoc reads it as docx-to-md converts it.

Pandoc reads Word well, but not everything as docx-to-md converts it. The copy shows the
document with every change accepted, keeps the text that fields and text boxes show, drops
hidden text, and says in terms Pandoc reads which paragraphs are headings, code and plain
text, and which image file each picture shows. The source document is never changed.
"""

from __future__ import annotations

import copy
import os
import posixpath
import re
import urllib.parse
import uuid
import zipfile
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx import publication as _publication
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import tables as _tables
from md_doc_converter.converters.docx.diagnostics import TransformReport, describe, transform
from md_doc_converter.converters.docx.models import ConversionError, ConversionPaths, WordPackage
from md_doc_converter.converters.docx.ooxml import (
    DRAWING,
    IMAGE_CONTAINERS,
    IMAGE_NODES,
    REL,
    SVG_DRAWING,
    TEXT_BOX_CONTENT,
    VML,
    WORD,
    XML_SPACE,
)

# The media files of the prepared copy: one per picture, so that Pandoc's image says which.
IMAGE_PREFIX = "docx-to-md-image-"
MISSING_BOOKMARK = "link to a missing Word bookmark; kept as plain text"
_EMPTY_HEADING = "Word heading has no text; omitted"
_PICTURE_HEADING = "Word heading has only a picture; kept as a paragraph"
_LISTED_HEADING = "Word numbers this outline-level heading as a list item; kept as a list item"
SKIPPED_LEVEL = "Word heading skips a level; kept at its level"
_UNLABELED_ITEMS = "Word list paragraph(s) without a number or bullet kept as paragraphs"
_LIST_GAPS = "empty paragraph(s) inside a Word list omitted"
# The names Word gives the bookmarks that its tables of contents and cross-references lead to.
_FIELD_BOOKMARKS = ("_Toc", "_Ref")
DRIFTED_BOOKMARK = (
    "Word table-of-contents or cross-reference bookmark starts before the paragraph it marks"
)
_TEXT_TAGS = frozenset(
    {WORD + "t", "{http://schemas.openxmlformats.org/officeDocument/2006/math}t"}
)
# Paragraph styles that Pandoc reads as metadata when they lead the document.
_METADATA_STYLES = frozenset({"title", "subtitle", "author", "date", "abstract"})
# Paragraph styles that make Pandoc take the paragraph as the caption of a table or picture
# beside it, away from its place.
_CAPTION_STYLES = frozenset({"caption", "table caption", "image caption"})
_CODE_BLOCK_STYLE = "Source Code"
_CODE_STYLE = "Verbatim Char"
# A paragraph holding any of these is not a code line: they are converted on their own.
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
_TEXT_BOX_CONSEQUENCE = "its text is kept after its paragraph"


@dataclass(frozen=True)
class PreparedImage:
    """A picture of the prepared copy: the package part it shows, and its alt text."""

    part: str
    alt_text: str | None
    # The Word paragraph that holds it, counted from 0, for warnings.
    paragraph_index: int
    from_caption: bool = False
    from_heading: bool = False


@dataclass
class PreparedDocument:
    path: Path
    title: str | None
    # By the name of the media file that the picture shows in the prepared copy.
    images: dict[str, PreparedImage] = field(default_factory=dict[str, PreparedImage])
    # The text that starts the paragraph of links to each Word bookmark, in this order, which
    # the copy ends with when it has bookmarks.
    probe: str = ""
    bookmarks: list[str] = field(default_factory=list[str])


class _Styles:
    """The prepared copy's styles, with those that the preparation adds."""

    def __init__(self, styles: ET.Element) -> None:
        self.root = copy.deepcopy(styles)
        self.definitions = {
            node.get(WORD + "styleId", ""): node for node in self.root.findall(WORD + "style")
        }
        self._added: dict[tuple[str, str], str] = {}
        # Whether the preparation changed a style of the document's own.
        self.edited = False

    def name(self, identity: str) -> str | None:
        definition = self.definitions.get(identity)
        label = None if definition is None else definition.find(WORD + "name")
        return None if label is None else label.get(WORD + "val")

    def chain(self, identity: str | None) -> list[str]:
        """The names of the style *identity* and of those it is based on, in lowercase."""
        names: list[str] = []
        seen: set[str] = set()
        while identity is not None and identity not in seen:
            seen.add(identity)
            definition = self.definitions.get(identity)
            if definition is None:
                break
            names.append((self.name(identity) or identity).casefold())
            parent = definition.find(WORD + "basedOn")
            identity = None if parent is None else parent.get(WORD + "val")
        return names

    def style(self, kind: str, name: str) -> str:
        """The id of a style of *kind* named *name* that the preparation adds."""
        if (kind, name) not in self._added:
            base = "DocxToMd" + re.sub(r"\W", "", name.title())
            identity = next(
                candidate
                for candidate in (base, *(f"{base}{number}" for number in range(1, 99)))
                if candidate not in self.definitions
            )
            definition = ET.SubElement(
                self.root, WORD + "style", {WORD + "type": kind, WORD + "styleId": identity}
            )
            ET.SubElement(definition, WORD + "name", {WORD + "val": name})
            self.definitions[identity] = definition
            self._added[kind, name] = identity
        return self._added[kind, name]

    @property
    def changed(self) -> bool:
        return self.edited or bool(self._added)


def document_title(root: ET.Element) -> str | None:
    for paragraph in root.iter(WORD + "p"):
        style = paragraph.find(f"./{WORD}pPr/{WORD}pStyle")
        if style is not None and style.get(WORD + "val", "").casefold() == "title":
            title = _ooxml.paragraph_text(paragraph)
            if title:
                return title
    return None


def rebase_link(target: str, paths: ConversionPaths) -> str:
    """A relative link of the Word document, made relative to the Markdown output."""
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


def _set_style(paragraph: ET.Element, identity: str) -> None:
    properties = paragraph.find(WORD + "pPr")
    if properties is None:
        properties = ET.Element(WORD + "pPr")
        paragraph.insert(0, properties)
    for node in properties.findall(WORD + "pStyle"):
        properties.remove(node)
    properties.insert(0, ET.Element(WORD + "pStyle", {WORD + "val": identity}))


def _style_of(paragraph: ET.Element) -> str | None:
    style = paragraph.find(f"./{WORD}pPr/{WORD}pStyle")
    return None if style is None else style.get(WORD + "val")


def _unwrap(node: ET.Element, parents: dict[ET.Element, ET.Element]) -> None:
    """Replace *node* with its children."""
    parent = parents[node]
    position = list(parent).index(node)
    children = list(node)
    parent[position : position + 1] = children
    for child in children:
        parents[child] = parent


def _unlink_targetless_links(
    root: ET.Element, relationships: ET.Element, report: TransformReport
) -> None:
    """Keep the text of a hyperlink without a target, which Pandoc drops, as plain text."""
    known = {node.get("Id", "") for node in relationships}
    parents = {child: parent for parent in root.iter() for child in parent}
    for link in list(root.iter(WORD + "hyperlink")):
        identifier = link.get(REL + "id")
        if identifier is not None and identifier not in known:
            link.attrib.pop(REL + "id")
            report.skipped(
                "links",
                "Word hyperlink relationship is missing; its target is dropped",
                f"Word hyperlink relationship {identifier!r} is missing; its target is dropped",
            )
        if link.get(REL + "id") is None and not link.get(WORD + "anchor"):
            _unwrap(link, parents)


def _text_boxes_after_paragraphs(root: ET.Element, report: TransformReport) -> None:
    """Move the paragraphs of each text box after the paragraph it is anchored to.

    Pandoc reads a VML text box where it is, splitting its paragraph, which a list item does
    not survive, and drops the text of a DrawingML one without a VML copy.
    """
    parents = {child: parent for parent in root.iter() for child in parent}
    numbers = {
        paragraph: index for index, paragraph in enumerate(_ooxml.scoped_iter(root, WORD + "p"))
    }
    moved: dict[ET.Element, int] = {}
    for container in list(_ooxml.scoped_iter(root)):
        if container.tag not in {WORD + "pict", WORD + "drawing"}:
            continue
        paragraph = parents.get(container)
        while paragraph is not None and paragraph.tag != WORD + "p":
            paragraph = parents.get(paragraph)
        holder = None if paragraph is None else parents.get(paragraph)
        if paragraph is None or holder is None:
            continue
        content: list[ET.Element] = []
        for box in list(container.iter(TEXT_BOX_CONTENT)):
            owner = parents.get(box)
            nested = owner
            while nested is not None and nested is not container:
                if nested.tag == TEXT_BOX_CONTENT:
                    break
                nested = parents.get(nested)
            if owner is None or nested is not container:
                continue
            content.extend(list(box))
            owner.remove(box)
        if not content:
            continue
        kind = "VML text box" if container.tag == WORD + "pict" else "DrawingML text box"
        reason = f"{kind}: no embedded image; {_TEXT_BOX_CONSEQUENCE}"
        where = f"paragraph {numbers[paragraph] + 1}" if paragraph in numbers else "the document"
        report.skipped(
            "images",
            reason,
            f"{kind} at {where}: no embedded image; {_TEXT_BOX_CONSEQUENCE}",
            section="markdown",
        )
        position = list(holder).index(paragraph) + 1 + moved.get(paragraph, 0)
        holder[position:position] = content
        moved[paragraph] = moved.get(paragraph, 0) + len(content)
        if not _ooxml.unit_image_nodes(container):
            parents[container].remove(container)


def _keep_metadata_in_body(root: ET.Element, styles: _Styles, numbered: Collection[str]) -> None:
    """Keep the leading Title, Subtitle, Author, Date and Abstract paragraphs in the body.

    Pandoc reads them as metadata, which it does not write; without the style, they are
    paragraphs.
    """
    body = root.find(WORD + "body")
    for paragraph in [] if body is None else list(body):
        if paragraph.tag != WORD + "p" or _ooxml.pandoc_heading(paragraph, styles.definitions):
            return
        try:
            if _ooxml.is_list_item(paragraph, styles.definitions, numbered):
                return
        except ConversionError:
            return
        if _METADATA_STYLES & set(styles.chain(_style_of(paragraph))):
            _make_plain_paragraph(paragraph)
            continue
        if _ooxml.paragraph_text(paragraph) or _has_picture(paragraph):
            return


def _list_numbering(
    package: WordPackage, styles: _Styles, report: TransformReport
) -> tuple[frozenset[str], frozenset[str], bytes | None]:
    """Word's numbered lists, those of them that show no number or bullet, and the numbering
    part rewritten if list-style links changed it."""
    data = package.parts.get(package.numbering_part)
    if data is None:
        return frozenset(), frozenset(), None
    try:
        numbering = ET.fromstring(data)
    except ET.ParseError as error:
        report.failed("lists", ConversionError(f"Cannot read Word numbering: {error}"))
        return frozenset(), frozenset(), None
    linked = _ooxml.link_numbering_styles(numbering, styles.definitions)
    return (
        _ooxml.numbered_lists(numbering),
        _ooxml.unlabeled_lists(numbering),
        _ooxml.word_xml_bytes(numbering, data) if linked else None,
    )


def _unnumber_unlabeled(root: ET.Element, styles: _Styles, unlabeled: Collection[str]) -> int:
    """Turn off the numbering of the paragraphs of lists that show no number or bullet, which
    Pandoc would number; return how many.

    Word uses such a list to indent its paragraphs, which it shows as plain paragraphs. Pandoc
    keeps one after a list item in that item, and makes the others paragraphs.
    """
    count = 0
    for paragraph in root.iter(WORD + "p"):
        try:
            number = _ooxml.numbering_reference(paragraph, styles.definitions)
        except ConversionError:
            continue
        if number in unlabeled:
            _ooxml.turn_off_numbering(paragraph)
            count += 1
    return count


def _style_heading_level(paragraph: ET.Element, styles: _Styles) -> int | None:
    """The level of a heading by its style's name, as Pandoc reads it: "heading N"."""
    for name in styles.chain(_style_of(paragraph)):
        match = re.fullmatch(r"heading ([0-9]+)", name)
        if match is not None and int(match.group(1)) > 0:
            level = int(match.group(1))
            if level > 6:
                raise ConversionError(
                    f"Cannot faithfully express Word heading level {level} in Markdown"
                )
            return level
    return None


def _prepare_headings(
    root: ET.Element,
    styles: _Styles,
    list_item: Callable[[ET.Element], bool],
    in_table: Callable[[ET.Element], bool],
    report: TransformReport,
) -> tuple[list[ET.Element], list[ET.Element]]:
    """Give each heading a "heading N" style at its final level, and make the rest paragraphs.

    A heading by Word's outline level or style keeps its level, up to 6; one that shows no
    text, or only a picture, is a paragraph. A numbered heading by outline level, not by a
    heading style, stays the list item that Word numbers. A heading more than one level below
    the heading before it keeps its level too, and is reported, as markdownlint's MD001 would
    report it; headings in tables are left out of that. Return the headings, in document
    order, and the numbered headings that stay list items.
    """
    levels: dict[ET.Element, int] = {}
    listed: list[ET.Element] = []
    for index, paragraph in enumerate(_ooxml.scoped_iter(root, WORD + "p")):
        try:
            level = _ooxml.heading_level(paragraph, styles.definitions)
            if level is None:
                level = _style_heading_level(paragraph, styles)
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
                section="markdown" if "no Markdown equivalent" in reason else "word",
            )
            _make_plain_paragraph(paragraph)
            continue
        if level is None:
            continue
        if (
            not _ooxml.pandoc_heading(paragraph, styles.definitions)
            and not in_table(paragraph)
            and list_item(paragraph)
        ):
            report.skipped("headings", _LISTED_HEADING, f"paragraph {index + 1}: {_LISTED_HEADING}")
            listed.append(paragraph)
            continue
        if not _shows_text(paragraph):
            reason = _PICTURE_HEADING if _has_picture(paragraph) else _EMPTY_HEADING
            report.skipped("headings", reason, f"paragraph {index + 1}: {reason}")
            _make_plain_paragraph(paragraph)
            continue
        levels[paragraph] = level
    previous: int | None = None
    for paragraph, level in levels.items():
        if in_table(paragraph):
            continue
        if previous is not None and level > previous + 1:
            text = " ".join(_ooxml.paragraph_text(paragraph).split())
            report.warn(
                f"headings: Word heading {text!r}, at level {level}, follows a level "
                f"{previous} heading; kept at its level",
                kind=SKIPPED_LEVEL,
            )
        previous = level
    for paragraph, level in levels.items():
        _set_style(paragraph, styles.style("paragraph", f"heading {level}"))
        properties = paragraph.find(WORD + "pPr")
        if properties is not None:
            for name in ("outlineLvl", "numPr"):
                for node in properties.findall(WORD + name):
                    properties.remove(node)
        report.applied("headings")
    return list(levels), listed


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
                value = child.text or ""
                kept = value.lstrip() if edge == 0 else value.rstrip()
                if kept:
                    if kept != value:
                        moved = ET.Element(WORD + "t", {XML_SPACE: "preserve"})
                        moved.text = (
                            value[: len(value) - len(kept)] if edge == 0 else value[len(kept) :]
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


def _code_paragraph(lines: list[str], style: str) -> ET.Element:
    """One paragraph in a code style, holding *lines* separated by line breaks."""
    paragraph = ET.Element(WORD + "p")
    ET.SubElement(ET.SubElement(paragraph, WORD + "pPr"), WORD + "pStyle", {WORD + "val": style})
    run = ET.SubElement(paragraph, WORD + "r")
    for index, line in enumerate(lines):
        if index:
            ET.SubElement(run, WORD + "br")
        if line:
            ET.SubElement(run, WORD + "t", {XML_SPACE: "preserve"}).text = line
    return paragraph


def _prepare_code(
    root: ET.Element,
    styles: _Styles,
    package: WordPackage,
    headings: Collection[ET.Element],
    list_item: Callable[[ET.Element], bool],
    report: TransformReport,
) -> None:
    """Mark text in a monospace font as code, in the styles Pandoc reads as code.

    Consecutive body paragraphs whose text is all monospace, with the empty paragraphs between
    them, become one paragraph in the Source Code style, which Pandoc reads as a code block;
    so does a top-level one-cell table that holds only such paragraphs. Other monospace runs,
    outside headings, hyperlinks, and fields, take the Verbatim Char style, which Pandoc reads
    as inline code. Word's typographic quotes, dashes, and ellipses in code become ASCII.
    """
    body = root.find(WORD + "body")
    if body is None:
        return
    properties = _ooxml.RunProperties(package.styles)

    def plain(paragraph: ET.Element) -> bool:
        return (
            paragraph not in headings
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
        lines: list[str] = []
        for paragraph in group:
            value, straightened = _ooxml.straighten_code(_ooxml.code_text(paragraph))
            report.applied("code", "curly quote(s) and dash(es) straightened", straightened)
            lines.extend(line.rstrip() for line in value.split("\n"))
        # A line break that starts or ends the code leaves an empty line, which Word shows as
        # space around the code.
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        position = list(body).index(group[0])
        for paragraph in group:
            body.remove(paragraph)
        body.insert(position, _code_paragraph(lines, styles.style("paragraph", _CODE_BLOCK_STYLE)))

    in_blocks = {paragraph for group in groups for paragraph in group}
    for paragraph in list(_ooxml.scoped_iter(root, WORD + "p")):
        if paragraph in in_blocks or paragraph in headings:
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
            run_properties = first.find(WORD + "rPr")
            if run_properties is None:
                run_properties = ET.Element(WORD + "rPr")
                first.insert(0, run_properties)
            for old in run_properties.findall(WORD + "rStyle"):
                run_properties.remove(old)
            identity = styles.style("character", _CODE_STYLE)
            run_properties.insert(0, ET.Element(WORD + "rStyle", {WORD + "val": identity}))
            report.applied("code", "inline code span(s)")


def _image_part(node: ET.Element, relations: dict[str, ET.Element], package: WordPackage) -> str:
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
    name = _ooxml.part_name(package.document_part, relation.get("Target", ""))
    if name not in package.parts:
        raise ConversionError(f"Missing Word media part {name!r}")
    if not package.parts[name]:
        raise ConversionError(f"Empty Word media part {name!r}")
    return name


def _select_image(
    node: ET.Element,
    relations: dict[str, ET.Element],
    parents: dict[ET.Element, ET.Element],
    package: WordPackage,
    report: TransformReport,
    where: str,
) -> str:
    """The part an image node shows: Word's SVG version if it has a usable one."""
    svg = next(node.iter(SVG_DRAWING + "svgBlip"), None) if node.tag == DRAWING + "blip" else None
    if svg is not None:
        owner = parents.get(svg)
        try:
            name = _image_part(svg, relations, package)
            _media.validate_svg_bytes(package.parts[name], name)
        except ConversionError as error:
            report.skipped(
                "checks",
                "Word SVG version is unusable; its fallback image is used",
                f"{where}: {describe(error)}; its fallback image is used",
                section="detail",
            )
        else:
            node.set(REL + "embed", svg.get(REL + "embed", ""))
            node.attrib.pop(REL + "link", None)
            return name
        finally:
            # Pandoc prefers the SVG version; the preparation has chosen.
            if owner is not None:
                owner.remove(svg)
    name = _image_part(node, relations, package)
    if node.get(REL + "embed"):
        node.attrib.pop(REL + "link", None)
    return name


def _separate_vml_images(
    unit: ET.Element, rest: list[tuple[ET.Element, str]], parents: dict[ET.Element, ET.Element]
) -> None:
    """Give each image after the first of a VML picture a picture of its own, after it.

    Pandoc reads only the first image of a VML picture, such as one of several shapes.
    """
    run = parents.get(unit)
    holder = parents.get(run) if run is not None else None
    if run is None or holder is None or run.tag != WORD + "r":
        return
    position = list(holder).index(run)
    for node, identity in rest:
        parent = parents.get(node)
        if parent is None:
            continue
        parent.remove(node)
        picture = ET.Element(WORD + "r")
        shape = ET.SubElement(ET.SubElement(picture, WORD + "pict"), VML + "shape")
        ET.SubElement(shape, VML + "imagedata", {REL + "id": identity})
        position += 1
        holder.insert(position, picture)


def _prepare_images(
    root: ET.Element,
    relationships: ET.Element,
    package: WordPackage,
    headings: Collection[ET.Element],
    modified: dict[str, bytes],
    report: TransformReport,
) -> dict[str, PreparedImage]:
    """Point each picture at a media part of its own, holding the image it shows.

    Pandoc names an image by its media file, so the file says which picture it is, with the
    package part shown and the alt text: the picture's figure caption, else Word's alt text,
    else a heading directly above it.
    """
    parents = {child: parent for parent in root.iter() for child in parent}
    relations = {node.get("Id", ""): node for node in relationships}
    units = _ooxml.image_units(root)
    descriptions = _media.image_descriptions(root, units, headings)
    images: dict[str, PreparedImage] = {}
    folder = posixpath.dirname(package.document_part)
    for unit, description in zip(units, descriptions, strict=True):
        where = f"image at paragraph {description.paragraph_index + 1}"
        shown: list[tuple[ET.Element, str]] = []
        for node in _ooxml.unit_image_nodes(unit):
            try:
                part = _select_image(node, relations, parents, package, report, where)
            except ConversionError as error:
                for attribute in (REL + "embed", REL + "link", REL + "id"):
                    node.attrib.pop(attribute, None)
                report.skipped_error("images", error, where, "; image dropped")
                continue
            number = len(images) + 1
            name = f"{IMAGE_PREFIX}{number}{PurePosixPath(part).suffix.casefold()}"
            target = posixpath.join("media", name)
            modified[posixpath.join(folder, target)] = package.parts[part]
            identity = f"docxToMdImage{number}"
            ET.SubElement(
                relationships,
                _ooxml.PACKAGE_REL + "Relationship",
                {"Id": identity, "Type": REL[1:-1] + "/image", "Target": target},
            )
            node.set(REL + "embed" if node.tag == DRAWING + "blip" else REL + "id", identity)
            shown.append((node, identity))
            images[name] = PreparedImage(
                part,
                description.alt_text,
                description.paragraph_index,
                from_caption=description.from_caption,
                from_heading=description.from_heading,
            )
        if unit.tag != WORD + "drawing":
            _separate_vml_images(unit, shown[1:], parents)
    paragraphs = list(_ooxml.scoped_iter(root, WORD + "p"))
    numbers = {paragraph: index for index, paragraph in enumerate(paragraphs)}
    for container in _ooxml.image_containers_without_images(root):
        owner = parents.get(container)
        while owner is not None and owner.tag != WORD + "p":
            owner = parents.get(owner)
        where = f"paragraph {numbers[owner] + 1}" if owner in numbers else "the document"
        kind = _ooxml.container_kind(container)
        if kind == "horizontal line" and owner is not None and not _shows_text(owner):
            # Pandoc reads a horizontal line only as a paragraph of one run of one shape.
            owner[:] = [ET.Element(WORD + "r")]
            ET.SubElement(ET.SubElement(owner[0], WORD + "pict"), VML + "rect")
            report.applied("images", "horizontal line(s) kept")
            continue
        report.skipped(
            "images",
            f"{kind}: no embedded image; omitted",
            f"{kind} at {where}: no embedded image; omitted",
        )
    return images


def _join_split_lists(root: ET.Element, list_of: Callable[[ET.Element], str | None]) -> int:
    """Remove the empty paragraphs between two items of the same Word list; return how many.

    Word numbers a list on past them, but Pandoc ends a list at any paragraph that is not an
    item: the list began again after one, and its next item lost its level. A paragraph that
    holds a bookmark stays.
    """
    removed = 0
    for parent in list(root.iter()):
        current: str | None = None
        gap: list[ET.Element] = []
        for child in list(parent):
            number = list_of(child) if child.tag == WORD + "p" else None
            if number is not None:
                if gap and number == current:
                    for paragraph in gap:
                        parent.remove(paragraph)
                    removed += len(gap)
                current = number
            elif child.tag in _TRANSPARENT:
                continue
            elif (
                current is not None
                and child.tag == WORD + "p"
                and not _ooxml.shows_content(child)
                and not any(node.tag == WORD + "bookmarkStart" for node in child.iter())
            ):
                gap.append(child)
                continue
            else:
                current = None
            gap.clear()
    return removed


def _report_drifted_bookmarks(root: ET.Element, report: TransformReport) -> None:
    """Report each linked bookmark of a Word table of contents or cross-reference that starts
    before the paragraph it marks.

    Word puts such a bookmark around one paragraph, such as a heading. Text typed at the start
    of that paragraph goes into the bookmark, which then starts at the earlier place: Word's
    links lead there, and so do the Markdown's, until the table of contents is updated and the
    cross-references are inserted again.
    """
    linked = _ooxml.linked_bookmarks(root)
    starts = {
        node.get(WORD + "id"): node
        for node in root.iter(WORD + "bookmarkStart")
        if node.get(WORD + "name", "").startswith(_FIELD_BOOKMARKS)
        and node.get(WORD + "name") in linked
    }
    if not starts:
        return
    nodes = list(root.iter())
    order = {node: index for index, node in enumerate(nodes)}
    parents = {child: parent for parent in nodes for child in parent}

    def paragraph_of(node: ET.Element) -> ET.Element | None:
        current = parents.get(node)
        while current is not None and current.tag != WORD + "p":
            current = parents.get(current)
        return current

    def shows(first: int, last: int) -> bool:
        return any(
            node.tag == WORD + "sym"
            or node.tag in IMAGE_CONTAINERS
            or (node.tag in _TEXT_TAGS and (node.text or "").strip())
            for node in nodes[first:last]
        )

    for end in root.iter(WORD + "bookmarkEnd"):
        start = starts.get(end.get(WORD + "id"))
        paragraph = paragraph_of(end)
        if start is None or paragraph is None or paragraph is paragraph_of(start):
            continue
        # The end follows text of its paragraph, and something shows before that paragraph.
        if shows(order[paragraph], order[end]) and shows(order[start] + 1, order[paragraph]):
            text = " ".join(_ooxml.paragraph_text(paragraph).split())
            report.warn(
                f"bookmarks: Word bookmark {start.get(WORD + 'name')!r} starts before the "
                f"paragraph it marks, {text!r}; links to it lead where it starts, as in Word",
                kind=DRIFTED_BOOKMARK,
            )


def _prepare_links(
    root: ET.Element,
    relationships: ET.Element,
    paths: ConversionPaths,
    report: TransformReport,
) -> list[str]:
    """Keep each bookmark once, and give each hyperlink a target Pandoc reads as Word means it.

    Return the names of the bookmarks kept, in document order.
    """
    parents = {child: parent for parent in root.iter() for child in parent}
    kept: list[str] = []
    seen: set[str] = set()
    scope = set(_ooxml.scoped_iter(root))
    for bookmark in list(root.iter(WORD + "bookmarkStart")):
        name = bookmark.get(WORD + "name", "")
        parent = parents[bookmark]
        drop = parent.tag in {WORD + "tbl", WORD + "tr"} or name == "_GoBack"
        if bookmark in scope and not drop and (not name or name in seen):
            reason = (
                "Word bookmark has no name; dropped"
                if not name
                else "duplicate Word bookmark name; the later one is dropped"
            )
            report.skipped("bookmarks", reason, f"{reason}: {name!r}" if name else reason)
            drop = True
        if drop:
            parent.remove(bookmark)
        elif name not in seen:
            seen.add(name)
            kept.append(name)
    for node in list(root.iter(WORD + "bookmarkEnd")):
        parents[node].remove(node)
    relations = {node.get("Id", ""): node for node in relationships}
    for relation in relationships:
        if relation.get("Type", "").endswith("/hyperlink"):
            try:
                relation.set("Target", rebase_link(relation.get("Target", ""), paths))
            except ConversionError as error:
                report.skipped_error("links", error, "hyperlink target", "; target kept as written")
    for link in list(root.iter(WORD + "hyperlink")):
        anchor = link.get(WORD + "anchor")
        relation = relations.get(link.get(REL + "id") or "")
        target = relation.get("Target", "") if relation is not None else ""
        try:
            parsed = _reference.split_uri(target)
        except ConversionError:
            continue
        if anchor and relation is not None and (parsed.path or parsed.scheme or parsed.netloc):
            # A link to a place in another document: the fragment joins its target.
            joined = copy.deepcopy(relation)
            joined.set("Id", f"docxToMdLink{len(relationships)}")
            joined.set(
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
            relationships.append(joined)
            link.set(REL + "id", joined.get("Id", ""))
            link.attrib.pop(WORD + "anchor")
            continue
        local = anchor or (
            urllib.parse.unquote(parsed.fragment)
            if not parsed.path and not parsed.scheme and not parsed.netloc
            else ""
        )
        if local and local not in seen:
            _unwrap(link, parents)
            report.skipped(
                "links",
                MISSING_BOOKMARK,
                f"link to missing Word bookmark {local!r}; kept as plain text",
            )

    def renders_link(link: ET.Element) -> bool:
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
    return kept


def _prepare_tables(root: ET.Element, report: TransformReport) -> None:
    """Give Pandoc tables it reads whole: a row without cells, which shows nothing, breaks
    the whole table, and a table without rows goes; a narrow first body row would narrow it."""
    for problem in _tables.canonicalize_horizontal_merges(root, strict=False):
        report.warn(
            f"Legacy horizontal table merge kept as written: {problem}",
            kind="legacy horizontal table merge kept as written",
            section="markdown",
        )
    _tables.complete_table_grids(root)
    with transform(report, "tables"):
        report.applied("tables", "empty row(s) omitted", _tables.drop_empty_rows(root))
        parents = {child: parent for parent in root.iter() for child in parent}
        omitted = 0
        for table in reversed(list(root.iter(WORD + "tbl"))):
            for row in table.findall(WORD + "tr"):
                if row.find(WORD + "tc") is None:
                    table.remove(row)
            if table.find(WORD + "tr") is None:
                parents[table].remove(table)
                omitted += 1
        report.applied("tables", "table(s) without cells omitted", omitted)
    _tables.pad_first_body_rows(root)


def _probe_bookmarks(root: ET.Element, names: list[str], probe: str) -> None:
    """End the copy with a paragraph of *probe* and then a link to each bookmark in *names*.

    Pandoc gives a bookmark in a heading the heading's ID, and adjacent bookmarks the first
    one's name, and writes links to them with those; these links say which bookmark has which.
    """
    body = root.find(WORD + "body")
    if body is None or not names:
        return
    paragraph = ET.Element(WORD + "p")
    paragraph.append(_ooxml.word_run(probe))
    for name in names:
        paragraph.append(_ooxml.word_run(" "))
        link = ET.SubElement(paragraph, WORD + "hyperlink", {WORD + "anchor": name})
        link.append(_ooxml.word_run("x"))
    last = len(body) - 1 if len(body) and body[-1].tag == WORD + "sectPr" else len(body)
    body.insert(last, paragraph)


def _plain_layout(root: ET.Element, styles: _Styles) -> None:
    """Keep paragraphs where they are, as paragraphs: Pandoc takes a caption into the table or
    picture beside it, and reads a paragraph indented more than its style as a block quote."""
    for paragraph in root.iter(WORD + "p"):
        if _CAPTION_STYLES & set(styles.chain(_style_of(paragraph))):
            _make_plain_paragraph(paragraph)
        properties = paragraph.find(WORD + "pPr")
        if properties is not None:
            for indent in properties.findall(WORD + "ind"):
                properties.remove(indent)


def _write_package(package: WordPackage, destination: Path, modified: dict[str, bytes]) -> None:
    pending = dict(modified)
    with zipfile.ZipFile(destination, "w") as archive:
        for entry in package.entries:
            archive.writestr(entry, pending.pop(entry.filename, package.parts[entry.filename]))
        for name, data in pending.items():
            archive.writestr(name, data)


def prepare_document(
    package: WordPackage, paths: ConversionPaths, work: Path, report: TransformReport
) -> PreparedDocument:
    """Write the prepared copy of *package* to *work*; the report says what changed."""
    root = copy.deepcopy(package.root)
    relationships = copy.deepcopy(package.relationships)
    styles = _Styles(package.styles)
    modified: dict[str, bytes] = {}
    _ooxml.accept_view(root)
    with transform(report, "fields"):
        problems, pages = _ooxml.normalize_hyperlink_fields(root, relationships)
        for problem in problems:
            report.skipped(
                "links", "Word hyperlink field could not be decoded; kept as text", problem
            )
        report.applied("fields", "table-of-contents page number(s) dropped", pages)
    _unlink_targetless_links(root, relationships, report)
    bookmarks = {node.get(WORD + "name", "") for node in root.iter(WORD + "bookmarkStart")} - {""}
    for name in _ooxml.unlink_missing_references(root, bookmarks):
        report.skipped(
            "links",
            MISSING_BOOKMARK,
            f"cross-reference to missing Word bookmark {name!r}; kept as plain text",
        )
    with transform(report, "fields"):
        report.applied("fields", "simple field(s) kept", _ooxml.expand_simple_fields(root))
        for detail, count in _ooxml.repair_fields(root).items():
            report.applied("fields", detail, count)
        report.applied("fields", "check box(es) shown", _ooxml.form_check_boxes(root))
        report.applied("fields", "page number(s) kept as text", _ooxml.unlink_page_references(root))
    with transform(report, "hidden-text"):
        report.applied("hidden-text", count=_ooxml.drop_hidden_text(root, package.styles))
    with transform(report, "spaces"):
        report.applied("spaces", count=_ooxml.plain_spaces(root, package.styles))
    with transform(report, "underline"):
        report.applied("underline", count=_ooxml.drop_redundant_underline(root, package.styles))
        _ooxml.single_underlines(root)
        styles.edited |= bool(_ooxml.single_underlines(styles.root))
    # Pandoc reads only the first picture of a run, and none of its text.
    _ooxml.split_drawing_runs(root)
    _prepare_tables(root, report)
    _text_boxes_after_paragraphs(root, report)
    title = document_title(root)
    numbered, unlabeled, numbering = _list_numbering(package, styles, report)
    if numbering is not None:
        modified[package.numbering_part] = numbering
    with transform(report, "lists"):
        report.applied("lists", _UNLABELED_ITEMS, _unnumber_unlabeled(root, styles, unlabeled))
    parents = {child: parent for parent in root.iter() for child in parent}

    def in_table(node: ET.Element) -> bool:
        current = parents.get(node)
        while current is not None:
            if current.tag == WORD + "tbl":
                return True
            current = parents.get(current)
        return False

    def list_of(paragraph: ET.Element) -> str | None:
        """The numId of the Word list that *paragraph* is an item of, as Pandoc reads it."""
        if _ooxml.pandoc_heading(paragraph, styles.definitions):
            return None
        try:
            number = _ooxml.numbering_reference(paragraph, styles.definitions)
        except ConversionError:
            return None
        return number if number is not None and int(number) and number in numbered else None

    def list_item(paragraph: ET.Element) -> bool:
        """Whether *paragraph* is an item of a Word list, as Pandoc reads it."""
        return list_of(paragraph) is not None

    headings: list[ET.Element] = []
    listed: list[ET.Element] = []
    with transform(report, "headings"):
        headings, listed = _prepare_headings(root, styles, list_item, in_table, report)
    with transform(report, "code"):
        _prepare_code(root, styles, package, set(headings), list_item, report)
    images: dict[str, PreparedImage] = {}
    with transform(report, "images"):
        # A numbered heading kept as a list item still heads a picture directly below it.
        images = _prepare_images(
            root, relationships, package, {*headings, *listed}, modified, report
        )
    with transform(report, "lists"):
        report.applied("lists", _LIST_GAPS, _join_split_lists(root, list_of))
    _report_drifted_bookmarks(root, report)
    bookmarks = _prepare_links(root, relationships, paths, report)
    _keep_metadata_in_body(root, styles, numbered)
    _plain_layout(root, styles)
    probe = "DOCXTOMDBOOKMARKS" + uuid.uuid4().hex.upper()
    _probe_bookmarks(root, bookmarks, probe)
    modified[package.document_part] = _ooxml.word_xml_bytes(
        root, package.parts[package.document_part]
    )
    modified[package.relationships_part] = ET.tostring(
        relationships, encoding="utf-8", xml_declaration=True
    )
    if styles.changed and package.styles_part in package.parts:
        modified[package.styles_part] = _ooxml.word_xml_bytes(
            styles.root, package.parts[package.styles_part]
        )
    elif styles.changed:
        modified[package.styles_part] = ET.tostring(
            styles.root, encoding="utf-8", xml_declaration=True
        )
    prepared = work / "prepared.docx"
    _write_package(package, prepared, modified)
    return PreparedDocument(prepared, title, images, probe, bookmarks)
