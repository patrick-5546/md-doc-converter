"""Where each Word bookmark leads: its heading, or the section that holds it."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from md_doc_converter.converters.docx import bookmarks as _bookmarks
from md_doc_converter.converters.docx import package as _package
from md_doc_converter.converters.docx import prepare as _prepare
from md_doc_converter.converters.docx import publication as _publication
from md_doc_converter.converters.docx import transforms as _transforms
from md_doc_converter.converters.docx import tree as _tree
from md_doc_converter.converters.docx.diagnostics import TransformReport
from md_doc_converter.converters.docx.models import (
    BookmarkSection,
    ConversionError,
    DocumentNavigation,
)
from md_doc_converter.pandoc import JSONValue
from tests.support.docx import W, bookmark, cell, hyperlink, paragraph, row, table, write_docx


def navigate(
    body: list[ET.Element], tmp_path: Path, pandoc: str
) -> tuple[DocumentNavigation, list[JSONValue], TransformReport]:
    """The navigation of a Word document of *body*, as the conversion reads it."""
    source = tmp_path / "input.docx"
    write_docx(source, body)
    paths = _publication.validate_paths(source, tmp_path / "output.md")
    report = TransformReport()
    work = tmp_path / "work"
    work.mkdir()
    prepared = _prepare.prepare_document(_package.read_package(paths), paths, work, report)
    tree = _tree.read_docx(prepared.path, work / "media", pandoc)
    blocks = _tree.items(tree["blocks"])
    resolved = _bookmarks.read_probe(blocks, prepared.probe, prepared.bookmarks)
    _transforms.unwrap_containers(blocks)
    return _bookmarks.collect_navigation(blocks, resolved, report), blocks, report


def start(name: str) -> ET.Element:
    return ET.Element(W + "bookmarkStart", {W + "id": name, W + "name": name})


def end(name: str) -> ET.Element:
    return ET.Element(W + "bookmarkEnd", {W + "id": name})


def test_preamble_heading_body_and_table_bookmarks_lead_to_their_sections(
    tmp_path: Path, pandoc: str
) -> None:
    found, _blocks, _report = navigate(
        [
            bookmark("_Preamble", "Before the first heading"),
            bookmark("_Heading", "1. Overview", "Heading1"),
            bookmark("_Body", "A body target"),
            table(row(cell(bookmark("_Cell", "Cell heading", "Heading2"))), columns=1),
            bookmark("_After", "Still in the same section"),
        ],
        tmp_path,
        pandoc,
    )
    [heading] = found.headings
    sections = found.bookmark_sections
    assert sections["_Preamble"] == BookmarkSection(None, False)
    assert sections["_Heading"] == BookmarkSection(heading.key, True)
    for name in ("_Body", "_Cell", "_After"):
        assert sections[name] == BookmarkSection(heading.key, False), name
    assert found.bookmarks == {"_Preamble", "_Heading", "_Body", "_Cell", "_After"}


def test_bookmarks_with_nothing_shown_before_a_heading_lead_to_it(
    tmp_path: Path, pandoc: str
) -> None:
    ending = paragraph("Text that ends with a bookmark")
    ending.append(start("_End"))
    found, _blocks, _report = navigate(
        [
            paragraph("First", "Heading1"),
            bookmark("_Empty", ""),
            paragraph(""),
            paragraph("Target", "Heading2"),
            paragraph("Text"),
            bookmark("_Before", ""),
            table(row(cell("Cell")), columns=1),
            ending,
            paragraph("Last", "Heading2"),
        ],
        tmp_path,
        pandoc,
    )
    _first, target, last = (heading.key for heading in found.headings)
    sections = found.bookmark_sections
    assert sections["_Empty"] == BookmarkSection(target, True)
    assert sections["_Before"] == BookmarkSection(target, False)
    assert sections["_End"] == BookmarkSection(last, True)


def test_a_range_that_holds_prose_before_a_heading_stays_in_its_section(
    tmp_path: Path, pandoc: str
) -> None:
    prose = paragraph("Selected prose before the next heading")
    prose.insert(0, start("_Range"))
    following = paragraph("Next", "Heading1")
    following.append(end("_Range"))
    found, _blocks, _report = navigate(
        [paragraph("First", "Heading1"), prose, following], tmp_path, pandoc
    )
    assert found.bookmark_sections["_Range"] == BookmarkSection(found.headings[0].key, False)


def test_bookmarks_side_by_side_each_lead_to_the_same_place(tmp_path: Path, pandoc: str) -> None:
    # Pandoc keeps the first of adjacent bookmarks and leads links to the others to it.
    both = paragraph("Text")
    both[0:0] = [start("_First"), start("_Second")]
    found, _blocks, _report = navigate([paragraph("Heading", "Heading1"), both], tmp_path, pandoc)
    sections = found.bookmark_sections
    assert (
        sections["_First"] == sections["_Second"] == BookmarkSection(found.headings[0].key, False)
    )
    assert {"_First", "_Second"} <= found.bookmarks


def test_bookmark_spans_go_and_links_to_missing_bookmarks_become_text(
    tmp_path: Path, pandoc: str
) -> None:
    found, blocks, report = navigate(
        [
            bookmark("_Target", "Target text"),
            hyperlink("Kept", anchor="_Target"),
            hyperlink("Away", relation=None, anchor="_Missing"),
        ],
        tmp_path,
        pandoc,
    )
    assert not [element for element in _tree.walk(blocks) if element["t"] == "Span"]
    assert [element["t"] for element in _tree.walk(blocks) if element["t"] == "Link"] == ["Link"]
    assert found.link_targets == ["#_Target"]
    assert dict(report.records["links"].skipped) == {_prepare.MISSING_BOOKMARK: 1}


def test_a_lost_probe_is_an_error() -> None:
    blocks: list[JSONValue] = [_tree.element("Para", [_tree.string("Text")])]
    with pytest.raises(ConversionError, match="lost its bookmarks"):
        _bookmarks.read_probe(blocks, "PROBE", ["_Name"])
    assert _bookmarks.read_probe(blocks, "", []) == {}


def test_every_bookmark_of_a_heading_leads_to_it(tmp_path: Path, pandoc: str) -> None:
    # Pandoc gives a heading the ID of its first bookmark and keeps the others as spans.
    heading = paragraph("Overview", "Heading1")
    heading[1:1] = [start("_First"), start("_Second")]
    found, _blocks, _report = navigate([heading, paragraph("Text")], tmp_path, pandoc)
    [only] = found.headings
    sections = found.bookmark_sections
    assert sections["_First"] == sections["_Second"] == BookmarkSection(only.key, True)
