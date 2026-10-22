"""Reading the Word source: the sections bookmarks belong to, heading text, and the code style."""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import source as _source
from tests.support.docx import (
    W,
    bookmark,
    cell,
    paragraph,
    row,
    table,
    word_style,
)


def sections(
    body: list[ET.Element], headings: dict[ET.Element, _models.SourceHeading]
) -> dict[str, _models.BookmarkSection]:
    root = ET.Element(W + "body")
    root.extend(body)
    parents = {child: parent for parent in root.iter() for child in parent}
    return _source._word_bookmark_sections(root, parents, headings)


def test_heading_body_table_and_preamble_have_stable_section_identities() -> None:
    title = bookmark("_Heading", "1. Overview", "Heading1")
    cell_heading = bookmark("_Cell", "Cell heading", "Heading2")
    nested = table(row(cell(cell_heading)), columns=1)
    contained_heading = nested.find(f".//{W}p")
    assert contained_heading is not None
    found = sections(
        [
            bookmark("_Preamble", "Before the first heading"),
            title,
            bookmark("_Body", "A body target"),
            nested,
            bookmark("_After", "Still in the same body section"),
        ],
        {
            title: _models.SourceHeading("section", 1, False),
            contained_heading: _models.SourceHeading("cell-heading", 2, True),
        },
    )
    assert found["_Preamble"] == _models.BookmarkSection(None, False)
    assert found["_Heading"] == _models.BookmarkSection("section", True)
    for name in ("_Body", "_Cell", "_After"):
        assert found[name] == _models.BookmarkSection("section", False)


@pytest.mark.parametrize(
    "paragraph_container",
    [pytest.param(False, id="start-in-body"), pytest.param(True, id="start-in-paragraph")],
)
def test_ranges_with_first_visible_content_in_heading_target_that_heading(
    paragraph_container: bool,
) -> None:
    start = ET.Element(W + "bookmarkStart", {W + "id": "4", W + "name": "_Range"})
    end = ET.Element(W + "bookmarkEnd", {W + "id": "4"})
    title = paragraph("Target", "Heading3")
    if paragraph_container:
        wrapper = ET.Element(W + "p")
        wrapper.append(start)
        start = wrapper
    found = sections([start, title, end], {title: _models.SourceHeading("target", 3, False)})
    assert found["_Range"] == _models.BookmarkSection("target", True)


def test_range_containing_prose_before_heading_stays_in_its_original_section() -> None:
    first = paragraph("First", "Heading1")
    next_heading = paragraph("Next", "Heading1")
    found = sections(
        [
            first,
            ET.Element(W + "bookmarkStart", {W + "id": "4", W + "name": "_Range"}),
            paragraph("Selected prose before the next heading"),
            next_heading,
            ET.Element(W + "bookmarkEnd", {W + "id": "4"}),
        ],
        {
            first: _models.SourceHeading("first", 1, False),
            next_heading: _models.SourceHeading("next", 1, False),
        },
    )
    assert found["_Range"] == _models.BookmarkSection("first", False)


def test_bookmarks_with_nothing_visible_before_a_heading_go_to_it() -> None:
    first = paragraph("First", "Heading1")
    target = paragraph("Target", "Heading2")
    last = paragraph("Last", "Heading2")
    ending = paragraph("Text that ends with a bookmark")
    ending.append(ET.Element(W + "bookmarkStart", {W + "id": "7", W + "name": "_End"}))
    found = sections(
        [
            first,
            bookmark("_Toc", "", "Heading2"),
            bookmark("_Empty", ""),
            paragraph(""),
            target,
            bookmark("_Text", "", "Heading2"),
            paragraph("Text"),
            bookmark("_Table", ""),
            table(row(cell("Cell")), columns=1),
            ET.Element(W + "bookmarkStart", {W + "id": "8", W + "name": "_Body"}),
            ending,
            last,
        ],
        {
            first: _models.SourceHeading("first", 1, False),
            target: _models.SourceHeading("target", 2, False),
            last: _models.SourceHeading("last", 2, False),
        },
    )
    for name in ("_Toc", "_Empty"):
        assert found[name] == _models.BookmarkSection("target", True)
    for name in ("_Text", "_Table", "_Body"):
        assert found[name] == _models.BookmarkSection("target", False)
    assert found["_End"] == _models.BookmarkSection("last", True)


def test_equations_and_symbols_are_text_but_spaces_are_not() -> None:
    math = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
    equation = paragraph("", "Heading2")
    formula = ET.SubElement(ET.SubElement(equation, math + "oMath"), math + "r")
    ET.SubElement(formula, math + "t").text = "x"
    symbol = paragraph("", "Heading2")
    ET.SubElement(ET.SubElement(symbol, W + "r"), W + "sym", {W + "char": "F0FC"})
    assert _source._shows_text(equation)
    assert _source._shows_text(symbol)
    assert not _source._shows_text(paragraph("\xa0 ", "Heading2"))


def test_a_verbatim_char_style_is_reused_or_added_with_a_free_id() -> None:
    def package(*styles: ET.Element) -> _models.WordPackage:
        definitions = ET.Element(W + "styles")
        definitions.extend(styles)
        root = ET.Element(W + "document")
        return _models.WordPackage([], {}, root, ET.Element("Relationships"), definitions)

    existing = word_style("MyCode", "character")
    name = existing.find(W + "name")
    assert name is not None
    name.set(W + "val", "Verbatim Char")
    assert _source._code_style(package(existing)) == ("MyCode", None)
    identity, definition = _source._code_style(package(word_style("VerbatimChar")))
    assert identity == "VerbatimChar1"
    assert definition is not None
    assert definition.get(W + "type") == "character"
    assert definition.get(W + "styleId") == "VerbatimChar1"
    added = definition.find(W + "name")
    assert added is not None
    assert added.get(W + "val") == "Verbatim Char"
