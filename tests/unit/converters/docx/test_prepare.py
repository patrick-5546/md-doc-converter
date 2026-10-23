"""The private copy of a Word document that Pandoc reads: what the preparation changes."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from md_doc_converter.converters.docx import package as _package
from md_doc_converter.converters.docx import prepare as _prepare
from md_doc_converter.converters.docx.diagnostics import TransformReport
from md_doc_converter.converters.docx.ooxml import PACKAGE_REL, REL
from tests.support.docx import W, cell, hyperlink, paragraph, row, table

MATH = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


def document(*body: ET.Element) -> ET.Element:
    root = ET.Element(W + "document")
    ET.SubElement(root, W + "body").extend(body)
    return root


def test_hyperlinks_without_a_target_keep_their_text() -> None:
    # Pandoc drops a hyperlink with neither a relationship nor a bookmark, with its text.
    root = document(
        hyperlink("Plain"), hyperlink("Gone", relation="gone"), hyperlink("Kept", relation="site")
    )
    relationships = ET.Element(PACKAGE_REL + "Relationships")
    ET.SubElement(
        relationships,
        PACKAGE_REL + "Relationship",
        {"Id": "site", "Target": "https://example.test/", "TargetMode": "External"},
    )
    report = TransformReport()
    _prepare._unlink_targetless_links(root, relationships, report)
    assert [link.get(REL + "id") for link in root.iter(W + "hyperlink")] == ["site"]
    assert "".join(root.itertext()) == "PlainGoneKept"
    assert dict(report.records["links"].skipped) == {
        "Word hyperlink relationship is missing; its target is dropped": 1
    }


def test_the_probe_links_each_bookmark_after_the_document_content() -> None:
    root = document(paragraph("Text"), ET.Element(W + "sectPr"))
    _prepare._probe_bookmarks(root, ["_A", "_B"], "PROBE")
    body = root.find(W + "body")
    assert body is not None
    assert [child.tag for child in body] == [W + "p", W + "p", W + "sectPr"]
    probe = body[1]
    assert "".join(probe.itertext()).startswith("PROBE")
    assert [link.get(W + "anchor") for link in probe.iter(W + "hyperlink")] == ["_A", "_B"]
    unchanged = document(paragraph("Text"))
    _prepare._probe_bookmarks(unchanged, [], "PROBE")
    assert len(unchanged.findall(f"./{W}body/{W}p")) == 1


def test_rows_without_cells_go_and_so_do_tables_left_without_rows() -> None:
    # Pandoc drops every cell of a table with a row of none.
    root = document(
        table(row(), row(cell("A"), cell("B")), row()),
        table(row(), row()),
        table(),
        table(row(cell(table(row(), columns=1)), cell("x"))),
    )
    report = TransformReport()
    _prepare._prepare_tables(root, report)
    shapes = [
        [len(item.findall(W + "tc")) for item in found.findall(W + "tr")]
        for found in root.iter(W + "tbl")
    ]
    assert shapes == [[2], [2]]
    assert report.records["tables"].applied["table(s) without cells omitted"] == 3


def test_equations_and_symbols_show_text_but_spaces_do_not() -> None:
    equation = paragraph("", "Heading2")
    formula = ET.SubElement(ET.SubElement(equation, MATH + "oMath"), MATH + "r")
    ET.SubElement(formula, MATH + "t").text = "x"
    symbol = paragraph("", "Heading2")
    ET.SubElement(ET.SubElement(symbol, W + "r"), W + "sym", {W + "char": "F0FC"})
    assert _prepare._shows_text(equation)
    assert _prepare._shows_text(symbol)
    assert not _prepare._shows_text(paragraph("\xa0 ", "Heading2"))


def test_strict_namespaces_are_read_as_transitional_ones() -> None:
    picture = b"\x89PNG http://purl.oclc.org/ooxml/drawingml/main"
    parts = _package._transitional(
        {
            "word/document.xml": b'<w:document xmlns:w="http://purl.oclc.org/ooxml/'
            b'wordprocessingml/main" xmlns:r="http://purl.oclc.org/ooxml/officeDocument/'
            b'relationships"/>',
            "word/_rels/document.xml.rels": b'<Relationship Type="http://purl.oclc.org/ooxml/'
            b'officeDocument/relationships/image"/>',
            "word/media/image.png": picture,
        }
    )
    assert parts["word/document.xml"] == (
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        b'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
    )
    assert parts["word/_rels/document.xml.rels"] == (
        b'<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        b'relationships/image"/>'
    )
    assert parts["word/media/image.png"] == picture
