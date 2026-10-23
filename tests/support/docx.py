"""Synthetic DOCX regression coverage."""

from __future__ import annotations

import copy
import struct
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import ooxml as _ooxml

W = _ooxml.WORD


R = _ooxml.REL


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
)


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><path d="M0 0L10 10"/></svg>'


def paragraph(text: str = "", style: str | None = None, *, bold: bool = False) -> ET.Element:
    result = ET.Element(W + "p")
    if style is not None:
        props = ET.SubElement(result, W + "pPr")
        ET.SubElement(props, W + "pStyle", {W + "val": style})
    run = ET.SubElement(result, W + "r")
    if bold:
        ET.SubElement(ET.SubElement(run, W + "rPr"), W + "b")
    ET.SubElement(run, W + "t", {_ooxml.XML_SPACE: "preserve"}).text = text
    return result


def code_run(text: str, *, font: str = "Courier New") -> ET.Element:
    """A run of *text* in a monospace font."""
    return font_run(text, font)


def font_run(text: str, font: str) -> ET.Element:
    """A run of *text* in *font*, such as a monospace or symbol font."""
    result = _ooxml.word_run(text)
    properties = ET.Element(W + "rPr")
    ET.SubElement(properties, W + "rFonts", {W + "ascii": font, W + "hAnsi": font})
    result.insert(0, properties)
    return result


def formatted(run: ET.Element, tag: str, value: str | None = None) -> ET.Element:
    """Give *run* the direct formatting w:*tag*, such as u or dstrike, with an optional w:val."""
    properties = run.find(W + "rPr")
    if properties is None:
        properties = ET.Element(W + "rPr")
        run.insert(0, properties)
    ET.SubElement(properties, W + tag, {} if value is None else {W + "val": value})
    return run


def paragraph_of(*runs: ET.Element, style: str | None = None) -> ET.Element:
    """A paragraph of *runs*, in the paragraph style *style*."""
    result = ET.Element(W + "p")
    if style is not None:
        ET.SubElement(ET.SubElement(result, W + "pPr"), W + "pStyle", {W + "val": style})
    result.extend(runs)
    return result


def cell(*content: str | ET.Element, colspan: int = 1, merge: str | None = None) -> ET.Element:
    result = ET.Element(W + "tc")
    props = ET.SubElement(result, W + "tcPr")
    if colspan != 1:
        ET.SubElement(props, W + "gridSpan", {W + "val": str(colspan)})
    if merge is not None:
        ET.SubElement(props, W + "vMerge", {W + "val": merge})
    for item in content or ("",):
        result.append(paragraph(item) if isinstance(item, str) else copy.deepcopy(item))
    return result


def row(*cells: ET.Element, header: bool = False) -> ET.Element:
    result = ET.Element(W + "tr")
    if header:
        ET.SubElement(ET.SubElement(result, W + "trPr"), W + "tblHeader")
    result.extend(cells)
    return result


def table(*rows: ET.Element, columns: int = 2, caption: str | None = None) -> ET.Element:
    result = ET.Element(W + "tbl")
    props = ET.SubElement(result, W + "tblPr")
    if caption is not None:
        ET.SubElement(props, W + "tblCaption", {W + "val": caption})
    grid = ET.SubElement(result, W + "tblGrid")
    for _ in range(columns):
        ET.SubElement(grid, W + "gridCol", {W + "w": "2000"})
    result.extend(rows)
    return result


def bookmark(name: str, text: str, style: str | None = None) -> ET.Element:
    result = paragraph(text, style)
    result.insert(
        1 if style else 0,
        ET.Element(
            W + "bookmarkStart",
            {
                W + "id": "1",
                W + "name": name,
            },
        ),
    )
    result.append(ET.Element(W + "bookmarkEnd", {W + "id": "1"}))
    return result


def hyperlink(label: str, *, anchor: str | None = None, relation: str | None = None) -> ET.Element:
    paragraph_node = ET.Element(W + "p")
    attributes: dict[str, str] = {}
    if anchor is not None:
        attributes[W + "anchor"] = anchor
    if relation is not None:
        attributes[R + "id"] = relation
    node = ET.SubElement(paragraph_node, W + "hyperlink", attributes)
    node.append(_ooxml.word_run(label))
    return paragraph_node


def boundary_hyperlink_field() -> ET.Element:
    result = ET.Element(W + "p")
    first = _ooxml.word_run("Before ")
    ET.SubElement(first, W + "fldChar", {W + "fldCharType": "begin"})
    result.append(first)
    instruction = ET.SubElement(result, W + "r")
    ET.SubElement(instruction, W + "instrText").text = ' HYPERLINK "https://example.test/plain" '
    ET.SubElement(
        ET.SubElement(result, W + "r"),
        W + "fldChar",
        {
            W + "fldCharType": "separate",
        },
    )
    result.append(_ooxml.word_run("Cached label"))
    last = ET.SubElement(result, W + "r")
    ET.SubElement(last, W + "fldChar", {W + "fldCharType": "end"})
    ET.SubElement(last, W + "t", {_ooxml.XML_SPACE: "preserve"}).text = " After"
    return result


def page_reference(anchor: str, value: str = "1", *, simple: bool = False) -> list[ET.Element]:
    instruction = f' PAGEREF "{anchor}" \\h '
    if simple:
        field = ET.Element(W + "fldSimple", {W + "instr": instruction})
        field.append(_ooxml.word_run(value))
        return [field]
    runs: list[ET.Element] = []
    for kind in ("begin", "instruction", "separate", "text", "end"):
        run = ET.Element(W + "r")
        if kind == "instruction":
            ET.SubElement(run, W + "instrText").text = instruction
        elif kind == "text":
            run = _ooxml.word_run(value)
        else:
            ET.SubElement(run, W + "fldChar", {W + "fldCharType": kind})
        runs.append(run)
    return runs


def toc_entry(
    anchor: str, number: str, title: str, page: str, *, begin: bool = False, end: bool = False
) -> ET.Element:
    """A Word table-of-contents entry: a link holding number, tab, title, leader and page.

    As in Word, the leader tab and the page number are hidden in web view. *begin* and *end*
    add the TOC field marks that Word puts in the first and last entries.
    """
    result = ET.Element(W + "p")
    ET.SubElement(ET.SubElement(result, W + "pPr"), W + "pStyle", {W + "val": "TOC1"})
    if begin:
        for kind in ("begin", "instruction", "separate"):
            run = ET.SubElement(result, W + "r")
            if kind == "instruction":
                ET.SubElement(run, W + "instrText").text = ' TOC \\o "1-3" \\h \\z \\u '
            else:
                ET.SubElement(run, W + "fldChar", {W + "fldCharType": kind})
    link = ET.SubElement(result, W + "hyperlink", {W + "anchor": anchor, W + "history": "1"})
    link.append(_ooxml.word_run(number))
    ET.SubElement(ET.SubElement(link, W + "r"), W + "tab")
    link.append(_ooxml.word_run(title))
    hidden = [ET.Element(W + "r"), *page_reference(anchor, page)]
    ET.SubElement(hidden[0], W + "tab")
    for run in hidden:
        properties = ET.Element(W + "rPr")
        ET.SubElement(properties, W + "webHidden")
        run.insert(0, properties)
        link.append(run)
    if end:
        ET.SubElement(ET.SubElement(result, W + "r"), W + "fldChar", {W + "fldCharType": "end"})
    return result


def image(relation: str = "image", *, alt: str | None = "Diagram") -> ET.Element:
    result = ET.Element(W + "p")
    drawing = ET.SubElement(ET.SubElement(result, W + "r"), W + "drawing")
    inline = ET.SubElement(drawing, _ooxml.DRAWING_WORD + "inline")
    props = {"id": "1", "name": "Picture"}
    if alt is not None:
        props["descr"] = alt
    ET.SubElement(inline, _ooxml.DRAWING_WORD + "extent", {"cx": "95250", "cy": "95250"})
    ET.SubElement(inline, _ooxml.DRAWING_WORD + "docPr", props)
    graphic = ET.SubElement(inline, _ooxml.DRAWING + "graphic")
    data = ET.SubElement(
        graphic,
        _ooxml.DRAWING + "graphicData",
        {
            "uri": "http://schemas.openxmlformats.org/drawingml/2006/picture",
        },
    )
    picture_ns = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
    picture = ET.SubElement(data, picture_ns + "pic")
    fill = ET.SubElement(picture, picture_ns + "blipFill")
    ET.SubElement(fill, _ooxml.DRAWING + "blip", {R + "embed": relation})
    return result


def legacy_image(kind: str = "pict") -> ET.Element:
    result = ET.Element(W + "p")
    container = ET.SubElement(ET.SubElement(result, W + "r"), W + kind)
    shape = ET.SubElement(
        container,
        _ooxml.VML + "shape",
        {
            "id": "preview",
            "type": "#_x0000_t75",
            "style": "width:10pt;height:10pt",
        },
    )
    ET.SubElement(
        shape,
        _ooxml.VML + "imagedata",
        {
            R + "id": "image",
            _ooxml.OFFICE + "title": "Legacy preview",
        },
    )
    return result


def text_box(text: str, *, legacy: bool = False) -> ET.Element:
    """A paragraph holding a DrawingML (or legacy VML) text box around *text*."""
    result = ET.Element(W + "p")
    run = ET.SubElement(result, W + "r")
    if legacy:
        shape = ET.SubElement(
            ET.SubElement(run, W + "pict"),
            _ooxml.VML + "shape",
            {"id": "box", "type": "#_x0000_t202"},
        )
        box = ET.SubElement(shape, _ooxml.VML + "textbox")
    else:
        anchor = ET.SubElement(ET.SubElement(run, W + "drawing"), _ooxml.DRAWING_WORD + "anchor")
        ET.SubElement(anchor, _ooxml.DRAWING_WORD + "extent", {"cx": "95250", "cy": "95250"})
        ET.SubElement(anchor, _ooxml.DRAWING_WORD + "docPr", {"id": "2", "name": "Text Box"})
        data = ET.SubElement(
            ET.SubElement(anchor, _ooxml.DRAWING + "graphic"),
            _ooxml.DRAWING + "graphicData",
            {"uri": _ooxml.WORD_SHAPE[1:-1]},
        )
        box = ET.SubElement(
            ET.SubElement(data, _ooxml.WORD_SHAPE + "wsp"), _ooxml.WORD_SHAPE + "txbx"
        )
    ET.SubElement(box, _ooxml.TEXT_BOX_CONTENT).append(paragraph(text))
    return result


def grouped_image(relation: str = "image", *, alt: str = "Grouped diagram") -> ET.Element:
    """A paragraph holding a floating DrawingML group around one picture."""
    group_ns = "{http://schemas.microsoft.com/office/word/2010/wordprocessingGroup}"
    picture_ns = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
    result = ET.Element(W + "p")
    anchor = ET.SubElement(
        ET.SubElement(ET.SubElement(result, W + "r"), W + "drawing"),
        _ooxml.DRAWING_WORD + "anchor",
    )
    ET.SubElement(anchor, _ooxml.DRAWING_WORD + "extent", {"cx": "95250", "cy": "95250"})
    ET.SubElement(anchor, _ooxml.DRAWING_WORD + "docPr", {"id": "3", "name": "Group", "descr": alt})
    data = ET.SubElement(
        ET.SubElement(anchor, _ooxml.DRAWING + "graphic"),
        _ooxml.DRAWING + "graphicData",
        {"uri": group_ns[1:-1]},
    )
    picture = ET.SubElement(ET.SubElement(data, group_ns + "wgp"), picture_ns + "pic")
    fill = ET.SubElement(picture, picture_ns + "blipFill")
    ET.SubElement(fill, _ooxml.DRAWING + "blip", {R + "embed": relation})
    return result


def write_docx(
    path: Path,
    body: list[ET.Element],
    *,
    relations: tuple[tuple[str, str, str, str], ...] = (),
    media: dict[str, bytes] | None = None,
    extras: dict[str, bytes] | None = None,
    styles: tuple[ET.Element, ...] = (),
    document: str = "word/document.xml",
) -> None:
    """Write a Word package; the package relationships name its main *document* part."""
    root = ET.Element(W + "document")
    body_node = ET.SubElement(root, W + "body")
    body_node.extend(copy.deepcopy(body))
    ET.SubElement(body_node, W + "sectPr")
    package = ET.Element(_ooxml.PACKAGE_REL + "Relationships")
    ET.SubElement(
        package,
        _ooxml.PACKAGE_REL + "Relationship",
        {
            "Id": "office",
            "Type": R[1:-1] + "/officeDocument",
            "Target": document,
        },
    )
    document_relations = ET.Element(_ooxml.PACKAGE_REL + "Relationships")
    for identifier, kind, target, mode in relations:
        ET.SubElement(
            document_relations,
            _ooxml.PACKAGE_REL + "Relationship",
            {
                "Id": identifier,
                "Type": R[1:-1] + "/" + kind,
                "Target": target,
                "TargetMode": mode,
            },
        )
    types_ns = "{http://schemas.openxmlformats.org/package/2006/content-types}"
    content_types = ET.Element(types_ns + "Types")
    for extension, mime in (
        ("rels", "application/vnd.openxmlformats-package.relationships+xml"),
        ("xml", "application/xml"),
        ("png", "image/png"),
        ("svg", "image/svg+xml"),
        ("emf", "image/x-emf"),
        ("wmf", "image/x-wmf"),
    ):
        ET.SubElement(
            content_types, types_ns + "Default", {"Extension": extension, "ContentType": mime}
        )
    for name, mime in (
        (
            "/" + document,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        ),
        (
            "/word/styles.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
        ),
    ):
        ET.SubElement(content_types, types_ns + "Override", {"PartName": name, "ContentType": mime})
    style_part = ET.Element(W + "styles")
    for level in range(1, 7):
        style = ET.SubElement(
            style_part, W + "style", {W + "type": "paragraph", W + "styleId": f"Heading{level}"}
        )
        ET.SubElement(style, W + "name", {W + "val": f"heading {level}"})
        ET.SubElement(
            ET.SubElement(style, W + "pPr"), W + "outlineLvl", {W + "val": str(level - 1)}
        )
    style_part.extend(copy.deepcopy(list(styles)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, xml in (
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", package),
            (document, root),
            (_models.relationships_part_of(document), document_relations),
            ("word/styles.xml", style_part),
        ):
            archive.writestr(name, _ooxml.word_xml_bytes(xml))
        for name, data in (media or {}).items():
            archive.writestr("word/media/" + name, data)
        for name, data in (extras or {}).items():
            archive.writestr(name, data)


def synthetic_emf(variant: int = 0) -> bytes:
    """A 10 mm square EMF drawing; each *variant*, up to 39, draws a different rectangle."""
    records = [
        struct.pack("<IIii", 27, 16, 0, 0),
        struct.pack("<IIii", 54, 16, 100, 100),
        struct.pack("<IIiiii", 43, 24, 10 + variant, 10 + variant, 90 - variant, 90 - variant),
        struct.pack("<IIIII", 14, 20, 0, 0, 20),
    ]
    header = b"".join(
        (
            struct.pack("<II", 1, 108),
            struct.pack("<4i", 0, 0, 100, 100),
            struct.pack("<4i", 0, 0, 1000, 1000),
            struct.pack("<IIII", 0x464D4520, 0x10000, 108 + sum(map(len, records)), 5),
            struct.pack("<HHIII", 1, 0, 0, 0, 0),
            struct.pack("<4i", 100, 100, 10, 10),
            struct.pack("<IIIii", 0, 0, 0, 10000, 10000),
        )
    )
    return header + b"".join(records)


def numbered(text: str, level: int = 0, *, number: str = "1") -> ET.Element:
    result = paragraph(text)
    properties = ET.Element(W + "pPr")
    numbering = ET.SubElement(properties, W + "numPr")
    ET.SubElement(numbering, W + "ilvl", {W + "val": str(level)})
    ET.SubElement(numbering, W + "numId", {W + "val": number})
    result.insert(0, properties)
    return result


def word_style(
    identity: str,
    kind: str = "paragraph",
    *,
    based_on: str | None = None,
    number: str | None = None,
) -> ET.Element:
    """A Word style; *number* gives it list numbering, which a list style always has."""
    result = ET.Element(W + "style", {W + "type": kind, W + "styleId": identity})
    ET.SubElement(result, W + "name", {W + "val": identity})
    if based_on is not None:
        ET.SubElement(result, W + "basedOn", {W + "val": based_on})
    if number is not None:
        numbering = ET.SubElement(ET.SubElement(result, W + "pPr"), W + "numPr")
        ET.SubElement(numbering, W + "numId", {W + "val": number})
    return result


def numbering_xml(
    kind: str = "decimal",
    start: int = 7,
    *,
    style_link: str | None = None,
    second: str | None = None,
) -> bytes:
    """List 1 defines three levels; *style_link* adds list 2, which takes them from that style.

    *second* instead adds list 2 with its own three levels in that format, numbered from 1.
    """
    numbering = ET.Element(W + "numbering")
    abstract = ET.SubElement(numbering, W + "abstractNum", {W + "abstractNumId": "1"})
    if style_link is not None:
        ET.SubElement(abstract, W + "styleLink", {W + "val": style_link})
        linked = ET.SubElement(numbering, W + "abstractNum", {W + "abstractNumId": "2"})
        ET.SubElement(linked, W + "numStyleLink", {W + "val": style_link})
    for index in range(3):
        level = ET.SubElement(abstract, W + "lvl", {W + "ilvl": str(index)})
        for name, value in (
            ("start", str(start)),
            ("numFmt", kind),
            ("lvlText", f"%{index + 1}."),
        ):
            ET.SubElement(level, W + name, {W + "val": value})
    if second is not None:
        other = ET.SubElement(numbering, W + "abstractNum", {W + "abstractNumId": "2"})
        for index in range(3):
            level = ET.SubElement(other, W + "lvl", {W + "ilvl": str(index)})
            text = "\u2022" if second == "bullet" else f"%{index + 1}."
            for name, value in (("start", "1"), ("numFmt", second), ("lvlText", text)):
                ET.SubElement(level, W + name, {W + "val": value})
    lists = ("1", "2") if style_link is not None or second is not None else ("1",)
    for identity in lists:
        number = ET.SubElement(numbering, W + "num", {W + "numId": identity})
        ET.SubElement(number, W + "abstractNumId", {W + "val": identity})
    return _ooxml.word_xml_bytes(numbering)
