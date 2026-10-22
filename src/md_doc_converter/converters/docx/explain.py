"""Content-free features of Word items, for the docx-to-md --explain report.

Every value is a fixed label, a yes/no, or a count range; never document text, names, or
link targets. The report can therefore be shared without the document.
"""

from __future__ import annotations

import functools
import re
import unicodedata
import urllib.parse
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Concatenate, ParamSpec
from xml.etree import ElementTree as ET

from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx.diagnostics import TransformReport, marker_pattern
from md_doc_converter.converters.docx.models import WordPackage
from md_doc_converter.converters.docx.ooxml import (
    DRAWING,
    DRAWING_WORD,
    GRAPHIC_KINDS,
    IMAGE_CONTAINERS,
    MC,
    PACKAGE_REL,
    REL,
    SVG_DRAWING,
    TEXT_BOX_CONTENT,
    VML,
    WORD,
)

Features = dict[str, str]
P = ParamSpec("P")

_FIELDS = frozenset(
    {
        "ADDIN",
        "ASK",
        "AUTHOR",
        "AUTONUM",
        "AUTONUMLGL",
        "BIBLIOGRAPHY",
        "CITATION",
        "COMMENTS",
        "CREATEDATE",
        "DATE",
        "DOCPROPERTY",
        "EMBED",
        "EQ",
        "FILENAME",
        "FILLIN",
        "FORMCHECKBOX",
        "FORMDROPDOWN",
        "FORMTEXT",
        "GOTOBUTTON",
        "HYPERLINK",
        "IF",
        "INCLUDEPICTURE",
        "INCLUDETEXT",
        "INDEX",
        "KEYWORDS",
        "LASTSAVEDBY",
        "LINK",
        "LISTNUM",
        "MACROBUTTON",
        "MERGEFIELD",
        "NOTEREF",
        "NUMPAGES",
        "PAGE",
        "PAGEREF",
        "PRINTDATE",
        "QUOTE",
        "REF",
        "REVNUM",
        "SAVEDATE",
        "SECTION",
        "SECTIONPAGES",
        "SEQ",
        "SET",
        "SHAPE",
        "STYLEREF",
        "SUBJECT",
        "SYMBOL",
        "TC",
        "TIME",
        "TITLE",
        "TOC",
        "XE",
    }
)
_FORMATS = {
    "bullet": "bullet",
    "decimal": "decimal",
    "decimalZero": "decimal",
    "lowerLetter": "lower letter",
    "upperLetter": "upper letter",
    "lowerRoman": "Roman",
    "upperRoman": "Roman",
    "none": "no number",
}
_IMAGE_FORMATS = {
    "png": "png",
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "gif": "gif",
    "emf": "emf",
    "wmf": "wmf",
    "svg": "svg",
    "tif": "tiff",
    "tiff": "tiff",
    "bmp": "bmp",
}
_FILE_TYPES = frozenset(
    {
        "csv",
        "doc",
        "docm",
        "docx",
        "dotx",
        "gif",
        "htm",
        "html",
        "jpeg",
        "jpg",
        "json",
        "md",
        "msg",
        "one",
        "pdf",
        "png",
        "ppt",
        "pptx",
        "svg",
        "txt",
        "vsd",
        "vsdx",
        "xls",
        "xlsm",
        "xlsx",
        "xml",
        "zip",
    }
)
_STYLE_CLASSES = (
    (re.compile(r"heading\s*[1-9]"), "heading"),
    (re.compile(r"(?:sub)?title"), "title or subtitle"),
    (re.compile(r"author|date|abstract"), "metadata"),
    (re.compile(r"toc.*|table of contents.*"), "table of contents"),
    (re.compile(r".*caption.*"), "caption"),
    (re.compile(r".*list.*"), "list"),
    (re.compile(r"normal"), "Normal"),
)
_CONTAINERS = {
    WORD + "p": "paragraph",
    WORD + "hyperlink": "link",
    WORD + "body": "between paragraphs",
    WORD + "tc": "table cell",
    WORD + "fldSimple": "field",
    WORD + "sdtContent": "content control",
    WORD + "smartTag": "smart tag",
}
_CHARACTERS = {
    "\\": "backslash",
    "`": "backtick",
    "[": "bracket",
    "]": "bracket",
    "(": "parenthesis",
    ")": "parenthesis",
    "#": "hash",
    "*": "asterisk",
    "_": "underscore",
    "|": "pipe",
    "<": "angle",
    ">": "angle",
    "&": "ampersand",
    "~": "tilde",
}
_CATEGORIES = {
    "L": "letter",
    "N": "digit",
    "P": "punctuation",
    "S": "symbol",
    "Z": "space",
    "C": "control",
    "M": "mark",
}
_NOTE_FRAGMENT = re.compile(r"(?:footnote|endnote|comment)(?:-ref)?-\d+")
# The parts besides the document body that hold Word text, by relationship type.
_OTHER_STORIES = ("footnotes", "endnotes", "comments", "header", "footer")
# Any docx-to-md alignment marker: its prefix is MDCONVDOCX and 32 hexadecimal digits.
_MARKERS = re.compile(r"MDCONVDOCX[0-9A-F]{32}" + marker_pattern("").pattern)


def yes(flag: bool) -> str:
    return "yes" if flag else "no"


def cap(count: int) -> str:
    """A count as 0, 1, or 2+."""
    return str(count) if count < 2 else "2+"


def count_range(count: int) -> str:
    for limit, label in ((1, str(count)), (4, "2-4"), (9, "5-9"), (49, "10-49")):
        if count <= limit:
            return label
    return "50+"


def level_range(level: str) -> str:
    if not level.isdigit() or int(level) > 8:
        return "invalid"
    value = int(level)
    return str(value) if value <= 2 else ("3-4" if value <= 4 else "5-8")


def name_class(name: str) -> str:
    """The kind of a Word bookmark name, from Word's own prefixes."""
    if not name:
        return "empty"
    for prefix in ("_Toc", "_Ref", "_Hlk"):
        if name.startswith(prefix):
            return prefix
    if name.casefold() == "_goback":
        return "_GoBack"
    return "other hidden" if name.startswith("_") else "named"


def style_class(paragraph: ET.Element | None, styles: Mapping[str, ET.Element]) -> str:
    if paragraph is None:
        return "no paragraph"
    reference = paragraph.find(f"./{WORD}pPr/{WORD}pStyle")
    if reference is None:
        return "no style"
    definition = styles.get(reference.get(WORD + "val", ""))
    if definition is None:
        return "undefined style"
    label = definition.find(WORD + "name")
    name = (label.get(WORD + "val", "") if label is not None else "") or definition.get(
        WORD + "styleId", ""
    )
    return next(
        (shown for pattern, shown in _STYLE_CLASSES if pattern.fullmatch(name.casefold())),
        "other",
    )


def heading_source(paragraph: ET.Element, styles: Mapping[str, ET.Element]) -> str:
    """What makes a paragraph a heading, in the order docx-to-md checks."""
    properties = paragraph.find(WORD + "pPr")
    if properties is not None and properties.find(WORD + "outlineLvl") is not None:
        return "outline level"
    reference = None if properties is None else properties.find(WORD + "pStyle")
    name = None if reference is None else reference.get(WORD + "val", "")
    seen: set[str] = set()
    while name is not None and name not in seen:
        seen.add(name)
        if re.fullmatch(r"heading\s*[1-9]", name, re.IGNORECASE):
            return "heading style"
        definition = styles.get(name)
        if definition is None:
            break
        if definition.find(f"./{WORD}pPr/{WORD}outlineLvl") is not None:
            return "style outline level"
        based = definition.find(WORD + "basedOn")
        name = None if based is None else based.get(WORD + "val", "")
    return "unknown"


def numbering_source(
    paragraph: ET.Element | None, styles: Mapping[str, ET.Element]
) -> tuple[str, str, str]:
    """How Word numbers a paragraph (no, directly, by style), with its numId and level."""
    if paragraph is None:
        return "no", "", "0"
    direct = paragraph.find(f"./{WORD}pPr/{WORD}numPr")
    ilvl = None if direct is None else direct.find(WORD + "ilvl")
    level = "0" if ilvl is None else ilvl.get(WORD + "val", "0")
    number = None if direct is None else direct.find(WORD + "numId")
    if number is not None:
        return "directly", number.get(WORD + "val", ""), level
    reference = paragraph.find(f"./{WORD}pPr/{WORD}pStyle")
    name = None if reference is None else reference.get(WORD + "val", "")
    seen: set[str] = set()
    while name is not None and name not in seen:
        seen.add(name)
        definition = styles.get(name)
        if definition is None:
            break
        numbering = definition.find(f"./{WORD}pPr/{WORD}numPr")
        number = None if numbering is None else numbering.find(WORD + "numId")
        if numbering is not None and number is not None:
            style_level = numbering.find(WORD + "ilvl")
            if ilvl is None and style_level is not None:
                level = style_level.get(WORD + "val", "0")
            return "by style", number.get(WORD + "val", ""), level
        based = definition.find(WORD + "basedOn")
        name = None if based is None else based.get(WORD + "val", "")
    return "no", "", level


def numbered(how: str, number: str) -> str:
    return "turned off" if how != "no" and number == "0" else how


def contents(element: ET.Element) -> str:
    """What a paragraph or link holds, as fixed labels."""
    inside = list(element.iter())[1:]
    tags = {node.tag for node in inside}
    parts = [child.tag for run in element.iter(WORD + "r") for child in run]
    flags = (
        ("text", bool(_MARKERS.sub("", _ooxml.word_text(element)).strip())),
        ("image", bool(tags & IMAGE_CONTAINERS)),
        ("link", WORD + "hyperlink" in tags),
        ("field", bool(tags & {WORD + "fldChar", WORD + "fldSimple"})),
        ("note reference", bool(tags & {WORD + "footnoteReference", WORD + "endnoteReference"})),
        ("line break", WORD + "br" in parts or WORD + "cr" in parts),
        ("tab", WORD + "tab" in parts),
        ("symbol", WORD + "sym" in tags),
        ("text box", TEXT_BOX_CONTENT in tags),
        ("content control", WORD + "sdt" in tags),
    )
    return "+".join(label for label, present in flags if present) or "nothing"


def hidden(element: ET.Element) -> str:
    """Whether Word hides the visible text, which one converter may still show."""
    flags = [
        _ooxml.word_flag(run.find(f"./{WORD}rPr/{WORD}vanish"))
        for run in element.iter(WORD + "r")
        if _MARKERS.sub("", _ooxml.word_text(run)).strip()
    ]
    if flags and all(flags):
        return "all text"
    return "some text" if any(flags) else "no"


def image_kind(unit: ET.Element) -> str:
    if unit.tag == WORD + "object":
        return "embedded object preview"
    if unit.tag == WORD + "pict":
        return "VML group" if next(unit.iter(VML + "group"), None) is not None else "VML picture"
    data = next(unit.iter(DRAWING + "graphicData"), None)
    uri = "" if data is None else data.get("uri", "")
    if uri.endswith("/picture"):
        return "picture"
    if uri.endswith("/wordprocessingShape"):
        return "shape"
    return GRAPHIC_KINDS.get(uri, "other drawing")


def placement(unit: ET.Element) -> str:
    if unit.tag != WORD + "drawing":
        return "VML"
    if unit.find(DRAWING_WORD + "inline") is not None:
        return "inline"
    return "floating" if unit.find(DRAWING_WORD + "anchor") is not None else "other"


class Numbering:
    """The number format of each list level, as Pandoc reads it."""

    def __init__(self, data: bytes | None = None) -> None:
        self.levels: dict[tuple[str, str], str] = {}
        if not data:
            return
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return
        abstracts = {
            abstract.get(WORD + "abstractNumId", ""): {
                level.get(WORD + "ilvl", ""): self._format(level)
                for level in abstract.findall(WORD + "lvl")
            }
            for abstract in root.findall(WORD + "abstractNum")
        }
        for instance in root.findall(WORD + "num"):
            reference = instance.find(WORD + "abstractNumId")
            identity = "" if reference is None else reference.get(WORD + "val", "")
            levels = dict(abstracts.get(identity, {}))
            for override in instance.findall(WORD + "lvlOverride"):
                level = override.find(WORD + "lvl")
                if level is not None:
                    levels[override.get(WORD + "ilvl", "")] = self._format(level)
            number = instance.get(WORD + "numId", "")
            self.levels.update({(number, ilvl): kind for ilvl, kind in levels.items()})

    @staticmethod
    def _format(level: ET.Element) -> str:
        node = level.find(WORD + "numFmt")
        if node is None:
            alternate = level.find(MC + "AlternateContent") is not None
            return "only in AlternateContent" if alternate else "no format"
        return _FORMATS.get(node.get(WORD + "val", ""), "other")

    def format(self, number: str, level: str) -> str:
        return self.levels.get((number, level), "no level")


def _records(
    method: Callable[Concatenate[Explainer, P], None],
) -> Callable[Concatenate[Explainer, P], None]:
    """An --explain step that must never change the conversion; a failure is counted."""

    @functools.wraps(method)
    def guarded(self: Explainer, /, *args: P.args, **kwargs: P.kwargs) -> None:
        try:
            method(self, *args, **kwargs)
        except Exception:  # noqa: BLE001 - --explain must never change the conversion
            self.report.census["items not described"] += 1

    return guarded


def _describes(
    method: Callable[Concatenate[Explainer, P], Features],
) -> Callable[Concatenate[Explainer, P], Features]:
    """An --explain shape that must never change the conversion."""

    @functools.wraps(method)
    def guarded(self: Explainer, /, *args: P.args, **kwargs: P.kwargs) -> Features:
        try:
            return method(self, *args, **kwargs)
        except Exception:  # noqa: BLE001 - --explain must never change the conversion
            return {"shape": "not described"}

    return guarded


class Explainer:
    """Record the features of each Word item docx-to-md aligns; only used with --explain."""

    def __init__(self, package: WordPackage, report: TransformReport) -> None:
        self.package = package
        self.report = report
        self.styles = {node.get(WORD + "styleId", ""): node for node in package.styles}
        self.numbering = Numbering()
        self.parents: dict[ET.Element, ET.Element] = {}
        self.relationships: ET.Element | None = None
        self.headings: set[ET.Element] = set()
        self._relations: dict[str, ET.Element] = {}
        self._relation_count = -1
        self._last_list = -2
        self._names: tuple[set[str], set[str]] | None = None

    @_records
    def count_fields(self, root: ET.Element) -> None:
        """Word fields by kind, before docx-to-md normalizes them."""
        census = self.report.field_census
        stack: list[str] = []
        for node in root.iter():
            if node.tag == WORD + "fldSimple":
                census[_field_kind(node.get(WORD + "instr", "")) + " (simple)"] += 1
            elif node.tag == WORD + "fldChar":
                kind = node.get(WORD + "fldCharType")
                if kind == "begin":
                    stack.append("")
                elif kind == "end" and stack:
                    census[_field_kind(stack.pop())] += 1
            elif node.tag == WORD + "instrText" and stack:
                stack[-1] += node.text or ""
        for instruction in stack:
            census[_field_kind(instruction) + " (not closed)"] += 1

    @_records
    def start(
        self,
        root: ET.Element,
        parents: dict[ET.Element, ET.Element],
        relationships: ET.Element,
    ) -> None:
        self.parents = parents
        self.relationships = relationships
        census = self.report.census
        census["paragraphs"] = sum(1 for _paragraph in _ooxml.scoped_iter(root, WORD + "p"))
        census["text boxes"] = sum(1 for _box in root.iter(TEXT_BOX_CONTENT))

    @_records
    def heading(self, token: str, paragraph: ET.Element, level: int) -> None:
        self.headings.add(paragraph)
        self.report.describe_item(
            token,
            "heading",
            {
                "heading by": heading_source(paragraph, self.styles),
                "level": str(level),
                **self.paragraph(paragraph),
            },
        )

    @_describes
    def heading_shape(self, paragraph: ET.Element) -> Features:
        """For a heading docx-to-md does not mark, such as one at level 7."""
        how, number, _level = numbering_source(paragraph, self.styles)
        return {
            "heading by": heading_source(paragraph, self.styles),
            "style": style_class(paragraph, self.styles),
            "numbered": numbered(how, number),
        }

    @_records
    def list_item(
        self, token: str, paragraph: ET.Element, index: int, *, continuation: bool, heading: bool
    ) -> None:
        how, number, level = numbering_source(paragraph, self.styles)
        after, self._last_list = index == self._last_list + 1, index
        kind = "list item"
        if continuation:
            kind = "numbering turned off" if number == "0" else "no list definition"
        elif heading:
            kind = "numbered heading"
        self.report.describe_item(
            token,
            "list item",
            {
                "kind": kind,
                "numbered": numbered(how, number),
                "level": level_range(level),
                "format": "none" if continuation else self.numbering.format(number, level),
                "after a list item": yes(after),
                **self.paragraph(paragraph),
            },
        )

    @_records
    def link(self, token: str, link: ET.Element) -> None:
        paragraph = self._ancestor(link, WORD + "p")
        self.report.describe_item(
            token,
            "link",
            {
                "target": self._link_target(link),
                "contains": contents(link),
                **self._place(link, paragraph),
            },
        )

    @_records
    def bookmark(self, token: str, marker: ET.Element, parent: ET.Element, name: str) -> None:
        paragraph = parent if parent.tag == WORD + "p" else self._ancestor(parent, WORD + "p")
        self.report.describe_item(
            token,
            "bookmark",
            {
                "name": name_class(name),
                "placed in": _CONTAINERS.get(parent.tag, "other markup"),
                "position": self._position(marker, paragraph),
                **self._place(parent, paragraph),
            },
        )

    @_records
    def image(self, token: str, unit: ET.Element, alt_text: str | None) -> None:
        paragraph = self._ancestor(unit, WORD + "p")
        nodes = _ooxml.unit_image_nodes(unit)
        formats = sorted({self._image_format(node) for node in nodes})
        self.report.describe_item(
            token,
            "image",
            {
                "kind": image_kind(unit),
                "placement": placement(unit),
                "images": cap(len(nodes)),
                "format": "+".join(formats) or "none",
                "SVG version": yes(next(unit.iter(SVG_DRAWING + "svgBlip"), None) is not None),
                "alt text": yes(bool(alt_text)),
                "in link": yes(self._ancestor(unit, WORD + "hyperlink") is not None),
                **self._place(unit, paragraph),
            },
        )

    @_describes
    def container_shape(self, container: ET.Element) -> Features:
        """For a drawing with no image to place, such as a text box."""
        return {
            "placement": placement(container),
            **self._place(container, self._ancestor(container, WORD + "p")),
        }

    @_records
    def table(self, token: str, table: ET.Element, nested: bool) -> None:
        rows = table.findall(WORD + "tr")
        cells = [cell for row in rows for cell in row.findall(WORD + "tc")]
        spans = [cell.find(f"./{WORD}tcPr/{WORD}gridSpan") for cell in cells]
        merged = any(
            span is not None and span.get(WORD + "val", "1") != "1" for span in spans
        ) or any(cell.find(f"./{WORD}tcPr/{WORD}vMerge") is not None for cell in cells)
        headers = sum(1 for row in rows if row.find(f"./{WORD}trPr/{WORD}tblHeader") is not None)
        gaps = any(
            row.find(f"./{WORD}trPr/{WORD}{name}") is not None
            for row in rows
            for name in ("gridBefore", "gridAfter")
        )
        inside = list(table.iter())[1:]
        tags = {node.tag for node in inside}
        listed = any(
            numbering_source(paragraph, self.styles)[0] != "no"
            for paragraph in table.iter(WORD + "p")
        )
        flags = (
            ("list", listed),
            ("image", bool(tags & IMAGE_CONTAINERS)),
            ("table", WORD + "tbl" in tags),
            ("link", WORD + "hyperlink" in tags),
            ("field", bool(tags & {WORD + "fldChar", WORD + "fldSimple"})),
            ("cell with paragraphs", any(len(cell.findall(WORD + "p")) > 1 for cell in cells)),
        )
        self.report.describe_item(
            token,
            "table",
            {
                "nested": yes(nested),
                "columns": count_range(len(table.findall(f"./{WORD}tblGrid/{WORD}gridCol"))),
                "rows": count_range(len(rows)),
                "merged cells": yes(merged),
                "header rows": cap(headers),
                "grid gaps": yes(gaps),
                "contains": "+".join(label for label, present in flags if present) or "text",
            },
        )

    @_describes
    def missing_bookmark(self, name: str, kind: str) -> Features:
        """Where a bookmark a link or cross-reference names exists instead, if anywhere."""
        original, other = self._bookmark_names()
        known = original | other
        if name in original:
            found = "only in deleted or moved text"
        elif name in other:
            found = "in notes, comments, headers or footers"
        elif name.casefold() in {item.casefold() for item in known}:
            found = "with different case"
        elif any(item.startswith(name) for item in known if len(item) > len(name)):
            found = "a longer name starts with it"
        else:
            found = "nowhere"
        return {"from": kind, "name": name_class(name), "bookmark": found}

    def paragraph(self, paragraph: ET.Element) -> Features:
        how, number, _level = numbering_source(paragraph, self.styles)
        return {
            "numbered": numbered(how, number),
            "Pandoc heading": yes(_ooxml.pandoc_heading(paragraph, self.styles)),
            "style": style_class(paragraph, self.styles),
            "in table": yes(self._ancestor(paragraph, WORD + "tc") is not None),
            "contains": contents(paragraph),
            "hidden": hidden(paragraph),
        }

    def _place(self, node: ET.Element, paragraph: ET.Element | None) -> Features:
        how, number, _level = numbering_source(paragraph, self.styles)
        return {
            "paragraph style": style_class(paragraph, self.styles),
            "numbered paragraph": numbered(how, number),
            "in heading": yes(paragraph in self.headings),
            "in table": yes(self._ancestor(node, WORD + "tc") is not None),
        }

    def _ancestor(self, node: ET.Element, tag: str) -> ET.Element | None:
        current = self.parents.get(node)
        while current is not None and current.tag != tag:
            current = self.parents.get(current)
        return current

    def _position(self, marker: ET.Element, paragraph: ET.Element | None) -> str:
        if paragraph is None:
            return "outside paragraphs"
        before: list[str] = []
        after: list[str] = []
        seen = False
        for node in paragraph.iter():
            if node is marker:
                seen = True
            elif node.tag == WORD + "t" and node.text:
                (after if seen else before).append(node.text)
        if not seen:
            return "not found"
        start = not _MARKERS.sub("", "".join(before)).strip()
        end = not _MARKERS.sub("", "".join(after)).strip()
        if start and end:
            return "empty paragraph"
        return "start" if start else ("end" if end else "middle")

    def _relation(self, identity: str) -> ET.Element | None:
        # docx-to-md adds relationships while it marks links.
        if self.relationships is not None and self._relation_count != len(self.relationships):
            self._relation_count = len(self.relationships)
            self._relations = {
                node.get("Id", ""): node
                for node in self.relationships
                if node.tag == PACKAGE_REL + "Relationship"
            }
        return self._relations.get(identity)

    def _link_target(self, link: ET.Element) -> str:
        anchor = link.get(WORD + "anchor")
        identity = link.get(REL + "id")
        if identity is None:
            return "bookmark" if anchor else "none"
        relation = self._relation(identity)
        if relation is None:
            return "missing relationship"
        target = relation.get("Target", "")
        if target.startswith("#"):
            return "bookmark"
        parsed = urllib.parse.urlsplit(target)
        scheme = parsed.scheme.casefold()
        fragment = " and bookmark" if parsed.fragment or anchor else ""
        if scheme == "mailto":
            return "email"
        if scheme in {"http", "https"}:
            return "web page" + fragment
        if scheme in {"", "file"} or len(scheme) == 1:
            return "local file" + fragment
        return "other"

    def _image_format(self, node: ET.Element) -> str:
        relation = self._relation(node.get(REL + "embed") or node.get(REL + "id") or "")
        if relation is None:
            return "missing"
        suffix = PurePosixPath(relation.get("Target", "")).suffix.casefold().lstrip(".")
        return _IMAGE_FORMATS.get(suffix, "other")

    def _bookmark_names(self) -> tuple[set[str], set[str]]:
        if self._names is None:
            original = {
                node.get(WORD + "name", "")
                for node in self.package.root.iter(WORD + "bookmarkStart")
            }
            other: set[str] = set()
            for kind in _OTHER_STORIES:
                for part in _ooxml.related_parts(
                    self.package.relationships, self.package.document_part, kind
                ):
                    try:
                        nodes = ET.fromstring(self.package.parts[part]).iter(WORD + "bookmarkStart")
                    except (KeyError, ET.ParseError):
                        continue
                    other.update(node.get(WORD + "name", "") for node in nodes)
            self._names = original - {""}, other - {""}
        return self._names


def _field_kind(instruction: str) -> str:
    match = re.match(r"\s*([A-Za-z]+)", instruction)
    keyword = match.group(1).upper() if match else ""
    return keyword if keyword in _FIELDS else ("none" if not keyword else "other")


def text_shape(text: str) -> str:
    """The kinds of Markdown in one stretch of a line, in order."""
    kinds: list[str] = []
    pattern = re.compile(
        rf"(?P<marker>{_MARKERS.pattern})|(?P<item>(?:[-+*]|\d+[.)])(?=\s))"
        r"|(?P<emphasis>[*_~`]+)|(?P<quote>>)|(?P<pipe>\|)|(?P<hash>#+)|(?P<space>\s+)"
        r"|(?P<image>!\[)|(?P<text>[^\s*_~`>|#!]+|!)"
    )
    for match in pattern.finditer(text):
        kind = match.lastgroup or "text"
        if kind == "item":
            kind = "list marker"
        if kind != "space" and (not kinds or kinds[-1] != kind):
            kinds.append(kind)
    return "+".join(kinds) or "nothing"


def character_class(character: str) -> str:
    if not character:
        return "end"
    if character in _CHARACTERS:
        return _CHARACTERS[character]
    return _CATEGORIES.get(unicodedata.category(character)[0], "other")


def _first_difference(theirs: str, ours: str) -> int:
    return next(
        (index for index, (a, b) in enumerate(zip(theirs, ours, strict=False)) if a != b),
        min(len(theirs), len(ours)),
    )


def size_range(count: int) -> str:
    for limit, label in ((1, "1 character"), (9, "2-9 characters"), (99, "10-99 characters")):
        if count <= limit:
            return label
    return "100+ characters"


def text_difference(theirs: str, ours: str) -> str:
    """How Pandoc's text differs from MarkItDown's, without quoting either."""
    if theirs == ours:
        return "same text"
    for longer, shorter, who in ((theirs, ours, "Pandoc"), (ours, theirs, "MarkItDown")):
        extra = size_range(len(longer) - len(shorter))
        if longer.startswith(shorter):
            return f"{who} has more at the end ({extra})"
        if longer.endswith(shorter):
            return f"{who} has more at the start ({extra})"
    index = _first_difference(theirs, ours)
    return (
        f"differ at the {'start' if index == 0 else 'middle'}: Pandoc "
        f"{character_class(theirs[index : index + 1])} / MarkItDown "
        f"{character_class(ours[index : index + 1])}"
    )


def difference_window(theirs: str, ours: str) -> str:
    """The kinds of characters around the first difference, on each side."""
    index = _first_difference(theirs, ours)

    def window(text: str) -> str:
        before = " ".join(character_class(item) for item in text[max(0, index - 3) : index])
        after = " ".join(character_class(item) for item in text[index : index + 4])
        return f"{before} | {after}".strip()

    return f"Pandoc {window(theirs)} / MarkItDown {window(ours)}"


def indent_range(line: str) -> str:
    indent = len(line) - len(line.lstrip(" \t"))
    return "0" if not indent else ("1-3" if indent < 4 else "4+")


def local_link_shape(target: str, destination: Path, word_link: bool) -> Features:
    """A retained relative link whose file is missing: its form, never its path."""
    parsed = urllib.parse.urlsplit(target)
    path = urllib.parse.unquote(parsed.path)
    suffix = PurePosixPath(path).suffix.casefold().lstrip(".")
    if parsed.scheme.casefold() == "file":
        form = "file URL"
    elif path.startswith("/"):
        form = "absolute path"
    elif path.startswith("../"):
        form = "parent folder"
    else:
        form = "same folder or below"
    if "\\" in path:
        form += " with backslashes"
    on_disk = "no"
    if destination.is_dir():
        on_disk = "a folder"
    elif _exists_ignoring_case(destination):
        on_disk = "with different case"
    return {
        "matches a Word hyperlink": yes(word_link),
        "file type": suffix if suffix in _FILE_TYPES else ("none" if not suffix else "other"),
        "form": form,
        "fragment": yes(bool(parsed.fragment)),
        "on disk": on_disk,
    }


def fragment_shape(fragment: str, same_page: bool) -> Features:
    if _NOTE_FRAGMENT.fullmatch(fragment):
        kind = "note reference"
    elif fragment.startswith("_"):
        kind = name_class(fragment)
    elif re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", fragment):
        kind = "heading-style ID"
    else:
        kind = "other"
    return {"fragment": kind, "points to": "this page" if same_page else "another page or file"}


def _exists_ignoring_case(path: Path) -> bool:
    """Whether *path* exists when its missing parts are matched ignoring case, as on Windows."""
    base, missing = path, list[str]()
    while not base.exists():
        if base.parent == base:
            return False
        missing.append(base.name)
        base = base.parent
    for part in reversed(missing):
        try:
            names = {child.name.casefold(): child.name for child in base.iterdir()}
        except OSError:
            return False
        if part.casefold() not in names:
            return False
        base /= names[part.casefold()]
    return True
