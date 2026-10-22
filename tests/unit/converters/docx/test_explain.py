"""Synthetic DOCX regression coverage."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from typing_extensions import override

from md_doc_converter.converters.docx import explain as _explain
from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx.diagnostics import TransformReport
from md_doc_converter.converters.docx.models import SourceDocument, WordPackage
from tests.support.docx import W, numbered, numbering_xml, paragraph, word_style

MARKER = "MDCONVDOCX" + "0123456789ABCDEF" * 2


def package(styles: tuple[ET.Element, ...] = ()) -> WordPackage:
    definitions = ET.Element(W + "styles")
    definitions.extend(styles)
    root = ET.Element(W + "document")
    return WordPackage([], {}, root, ET.Element("Relationships"), definitions)


class Broken(_explain.Explainer):
    """An explainer whose features fail, as a bug or an unexpected document would."""

    @override
    def paragraph(self, paragraph: ET.Element) -> _explain.Features:
        raise RuntimeError("unexpected")

    @override
    def _place(self, node: ET.Element, paragraph: ET.Element | None) -> _explain.Features:
        raise RuntimeError("unexpected")


class TextShapeTests(unittest.TestCase):
    def test_markdown_kinds_are_named_in_order_without_the_text(self) -> None:
        for text, expected in (
            (f"* 1. {MARKER}N4BEGIN words", "list marker+marker+text"),
            ("| > ## `x` ![a", "pipe+quote+hash+emphasis+text+emphasis+image+text"),
            ("  ", "nothing"),
            ("**Note:**", "emphasis+text+emphasis"),
        ):
            with self.subTest(text=text):
                assert _explain.text_shape(text) == expected

    def test_differences_are_classified_without_quoting(self) -> None:
        for theirs, ours, expected in (
            ("abc", "abc", "same text"),
            ("abcdef", "abc", "Pandoc has more at the end (2-9 characters)"),
            ("abc", "xabc", "MarkItDown has more at the start (1 character)"),
            ("ab1", "ab[", "differ at the middle: Pandoc digit / MarkItDown bracket"),
            ("\u00e9", "\\", "differ at the start: Pandoc letter / MarkItDown backslash"),
        ):
            with self.subTest(expected=expected):
                assert _explain.text_difference(theirs, ours) == expected
        assert (
            _explain.difference_window("abc", "-abc")
            == "Pandoc | letter letter letter / MarkItDown | punctuation letter letter letter"
        )

    def test_ranges(self) -> None:
        assert [_explain.count_range(count) for count in (0, 1, 3, 7, 12, 50)] == [
            "0",
            "1",
            "2-4",
            "5-9",
            "10-49",
            "50+",
        ]
        assert [_explain.level_range(level) for level in ("0", "2", "4", "8", "9", "x")] == [
            "0",
            "2",
            "3-4",
            "5-8",
            "invalid",
            "invalid",
        ]
        assert [_explain.indent_range(line) for line in ("x", "  x", "    x")] == ["0", "1-3", "4+"]
        assert [
            _explain.name_class(name) for name in ("_Toc1", "_Ref2", "_GoBack", "_x", "Spec", "")
        ] == ["_Toc", "_Ref", "_GoBack", "other hidden", "named", "empty"]


class WordFeatureTests(unittest.TestCase):
    def test_heading_and_numbering_sources_follow_the_style_chain(self) -> None:
        base = word_style("Base", number="1")
        child = word_style("Child", based_on="Base")
        styles = {"Base": base, "Child": child, "Heading2": word_style("Heading2")}
        outlined = paragraph("text")
        properties = ET.Element(W + "pPr")
        ET.SubElement(properties, W + "outlineLvl", {W + "val": "1"})
        outlined.insert(0, properties)
        assert _explain.heading_source(outlined, styles) == "outline level"
        assert _explain.heading_source(paragraph("text", "Heading2"), styles) == "heading style"
        assert _explain.heading_source(paragraph("text", "Child"), styles) == "unknown"
        assert _explain.numbering_source(paragraph("text", "Child"), styles) == (
            "by style",
            "1",
            "0",
        )
        assert _explain.numbering_source(numbered("x", 2), styles) == ("directly", "1", "2")
        assert _explain.numbering_source(paragraph("x"), styles) == ("no", "", "0")
        assert _explain.numbered("directly", "0") == "turned off"
        assert _explain.style_class(paragraph("x", "Heading2"), styles) == "heading"
        assert _explain.style_class(paragraph("x", "Missing"), styles) == "undefined style"
        assert _explain.style_class(paragraph("x"), styles) == "no style"

    def test_contents_ignore_alignment_markers(self) -> None:
        marked = paragraph(f"{MARKER}H3BEGIN")
        assert _explain.contents(marked) == "nothing"
        run = ET.SubElement(marked, W + "r")
        ET.SubElement(run, W + "footnoteReference", {W + "id": "1"})
        ET.SubElement(run, W + "tab")
        assert _explain.contents(marked) == "note reference+tab"
        hidden = paragraph("words")
        run = hidden.find(W + "r")
        assert run is not None
        ET.SubElement(ET.SubElement(run, W + "rPr"), W + "vanish")
        assert _explain.hidden(hidden) == "all text"

    def test_numbering_formats_include_overrides_and_alternate_content(self) -> None:
        numbering = ET.fromstring(numbering_xml("lowerLetter"))
        instance = numbering.find(W + "num")
        assert instance is not None
        override = ET.SubElement(instance, W + "lvlOverride", {W + "ilvl": "1"})
        level = ET.SubElement(override, W + "lvl", {W + "ilvl": "1"})
        ET.SubElement(level, W + "numFmt", {W + "val": "bullet"})
        level = numbering.find(f"{W}abstractNum/{W}lvl[@{W}ilvl='2']")
        assert level is not None
        format_node = level.find(W + "numFmt")
        assert format_node is not None
        level.remove(format_node)
        ET.SubElement(level, _ooxml.MC + "AlternateContent")
        formats = _explain.Numbering(ET.tostring(numbering))
        assert [formats.format("1", level) for level in ("0", "1", "2", "3")] == [
            "lower letter",
            "bullet",
            "only in AlternateContent",
            "no level",
        ]

    def test_local_links_and_fragments_are_described_by_form(self) -> None:
        with tempfile.TemporaryDirectory(prefix="explain-links-") as temporary:
            root = Path(temporary)
            (root / "Specs").mkdir()
            (root / "Specs" / "Guide.docx").write_bytes(b"")
            shapes = [
                _explain.local_link_shape(target, root / path, word)
                for target, path, word in (
                    ("Specs", "Specs", True),
                    ("specs/guide.docx#part", "specs/guide.docx", True),
                    ("../Other%20Spec.pdf", "missing.pdf", False),
                )
            ]
        assert [shape["on disk"] for shape in shapes] == ["a folder", "with different case", "no"]
        assert [shape["file type"] for shape in shapes] == ["none", "docx", "pdf"]
        assert [shape["form"] for shape in shapes] == [
            "same folder or below",
            "same folder or below",
            "parent folder",
        ]
        assert [shape["fragment"] for shape in shapes] == ["no", "yes", "no"]
        assert shapes[2]["matches a Word hyperlink"] == "no"
        assert _explain.fragment_shape("footnote-3", True) == {
            "fragment": "note reference",
            "points to": "this page",
        }
        assert _explain.fragment_shape("my-section", False)["fragment"] == "heading-style ID"

    def test_a_failing_feature_never_stops_the_conversion(self) -> None:
        report = TransformReport(explaining=True)
        explainer = Broken(package(), report)
        explainer.heading("token", paragraph("text"), 1)
        assert report.census["items not described"] == 1
        assert report.items == {}
        assert explainer.container_shape(ET.Element(W + "drawing")) == {"shape": "not described"}


class ExplanationTests(unittest.TestCase):
    def test_features_compare_skipped_items_with_all_items_of_their_kind(self) -> None:
        report = TransformReport(explaining=True)
        report.census["paragraphs"] = 9
        report.field_census["REF"] = 2
        for index, (style, level) in enumerate(
            (("a", "0"), ("a", "1"), ("b", "1"), ("c", "1"), ("d", "1"), ("e", "1"), ("f", "1"))
        ):
            report.describe_item(
                f"N{index}", "list item", {"style": style, "level": level, "in": "x"}
            )
        report.describe_item("H0", "heading", {"level": "2"})
        with contextlib.redirect_stderr(io.StringIO()):
            report.skipped("lists", "reason one", "N0", items=["N0", "N1"], shape={"why": "A"})
            report.skipped("lists", "reason one", "N2", items=["N2"], shape={"why": "B"})
            report.skipped("lists", "reason two", "plain detail")
        assert report.explanation() == [
            "docx-to-md: explain (fixed labels and counts only; no document text)",
            "  document: paragraphs 9; headings 1; list items 7",
            "  fields: REF 2",
            "  lists: reason one (2)",
            "    why: A 1; B 1",
            "    list items: 3 of 7",
            "      style: a 2/2; b 1/1; c 0/1; d 0/1; e 0/1; 1 more",
            "      level: 1 2/6; 0 1/1",
        ]

    def test_nothing_is_recorded_without_explain(self) -> None:
        report = TransformReport()
        report.describe_item("N0", "list item", {"style": "a"})
        with contextlib.redirect_stderr(io.StringIO()):
            report.skipped("lists", "reason", items=["N0"], shape={"why": "A"})
        assert report.explanation() == []
        assert (report.items, report.events) == ({}, [])
        assert report.records["lists"].skipped["reason"] == 1

    def test_an_attempt_keeps_its_features_only_when_merged(self) -> None:
        report = TransformReport(explaining=True)
        attempt = report.child()
        assert attempt.explaining
        attempt.describe_item("H0", "heading", {"level": "1"})
        attempt.skipped("headings", "reason", items=["H0"])
        attempt.census["paragraphs"] = 1
        with contextlib.redirect_stderr(io.StringIO()):
            report.merge(attempt)
        assert "H0" in report.items
        assert len(report.events) == 1
        assert report.census["paragraphs"] == 1


class SourceDocumentFeatureTests(unittest.TestCase):
    def test_explainer_records_items_by_marker(self) -> None:
        styles = (word_style("Heading1"),)
        report = TransformReport(explaining=True)
        explainer = _explain.Explainer(package(styles), report)
        body = ET.Element(W + "body")
        heading = paragraph("Title", "Heading1")
        item = numbered("Item")
        body.extend([heading, item])
        source = SourceDocument(body, MARKER)
        parents = {child: parent for parent in source.root.iter() for child in parent}
        explainer.start(source.root, parents, ET.Element("Relationships"))
        explainer.numbering = _explain.Numbering(numbering_xml())
        explainer.heading(MARKER + "H0", heading, 1)
        explainer.list_item(MARKER + "N1", item, 1, continuation=False, heading=False)
        assert report.census["paragraphs"] == 2
        assert report.items[MARKER + "H0"][1]["heading by"] == "heading style"
        kind, features = report.items[MARKER + "N1"]
        assert kind == "list item"
        assert {
            name: features[name] for name in ("kind", "numbered", "format", "after a list item")
        } == {
            "kind": "list item",
            "numbered": "directly",
            "format": "decimal",
            "after a list item": "no",
        }


if __name__ == "__main__":
    unittest.main()
