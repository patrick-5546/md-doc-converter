"""Synthetic DOCX regression coverage."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch
from xml.etree import ElementTree as ET

from typing_extensions import override

from md_doc_converter.converters.docx import diagnostics as _diagnostics
from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import normalization as _normalization
from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx import pipeline as _pipeline
from md_doc_converter.converters.docx import publication as _publication
from md_doc_converter.converters.docx import recovery as _recovery
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import source as _source
from md_doc_converter.converters.docx import tables as _tables
from md_doc_converter.converters.docx import validation as _validation
from tests.support.docx import (
    PNG,
    SVG,
    R,
    W,
    bookmark,
    boundary_hyperlink_field,
    cell,
    code_run,
    grouped_image,
    hyperlink,
    image,
    legacy_image,
    numbered,
    numbering_xml,
    page_reference,
    paragraph,
    paragraph_of,
    row,
    synthetic_emf,
    table,
    text_box,
    toc_entry,
    word_style,
    write_docx,
)

COMMAND = [sys.executable, "-m", "md_doc_converter", "docx-to-md"]


class ConversionTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="docx-conversion-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "Synthetic Guide.docx"
        self.pandoc = _reference.require_pandoc()

    def convert(
        self,
        body: list[ET.Element],
        *,
        output: Path | None = None,
        split_depth: int = 0,
        qualify: bool = False,
        relations: tuple[tuple[str, str, str, str], ...] = (),
        media: dict[str, bytes] | None = None,
        report: _diagnostics.TransformReport | None = None,
        extras: dict[str, bytes] | None = None,
        styles: tuple[ET.Element, ...] = (),
    ) -> tuple[str, dict[str, int]]:
        write_docx(
            self.source, body, relations=relations, media=media, extras=extras, styles=styles
        )
        stats = _pipeline.convert_docx(
            self.source,
            output,
            split_depth=split_depth,
            qualify_duplicate_headings=qualify,
            report=report,
        )
        return _publication.requested_output(self.source, output).read_text(encoding="utf-8"), stats

    def convert_reported(
        self,
        body: list[ET.Element],
        *,
        output: Path | None = None,
        relations: tuple[tuple[str, str, str, str], ...] = (),
        media: dict[str, bytes] | None = None,
        extras: dict[str, bytes] | None = None,
        styles: tuple[ET.Element, ...] = (),
        explain: bool = False,
    ) -> tuple[str, _diagnostics.TransformReport, str]:
        """Convert with a transform report; return the output, the report, and the warnings."""
        report = _diagnostics.TransformReport(explaining=explain)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result, _stats = self.convert(
                body,
                output=output,
                relations=relations,
                media=media,
                report=report,
                extras=extras,
                styles=styles,
            )
        return result, report, stderr.getvalue()

    def test_default_keeps_step_and_status_labels_and_index_content(self) -> None:
        result, stats = self.convert(
            [
                paragraph("Title.", "Heading1"),
                paragraph("Gap.", "Heading4"),
                paragraph("1)", bold=True),
                paragraph("Do the thing."),
                paragraph("Closed.", bold=True),
                paragraph("Contents", "Heading1"),
                paragraph("Original index entry"),
                paragraph("Flow.", "Heading3"),
                paragraph("Flow.", "Heading3"),
            ]
        )
        # Headings lose trailing punctuation and skipped levels; bold labels keep theirs.
        self.assertIn("# Title\n", result)
        self.assertIn("## Gap\n", result)
        self.assertIn("**1)**\n\nDo the thing.", result)
        self.assertIn("**Closed.**", result)
        self.assertIn("# Contents", result)
        self.assertIn("Original index entry", result)
        self.assertEqual(result.count("## Flow\n"), 2)
        self.assertEqual(stats["qualified_headings"], 0)
        self.assertTrue(result.endswith("\n"))
        self.assertFalse(result.endswith("\n\n"))

    def test_ordered_list_start_and_nesting_survive_conversion_and_splitting(self) -> None:
        write_docx(
            self.source,
            [
                paragraph("Numbers.", "Heading1"),
                numbered("Seven"),
                numbered("Nested seven", 1),
                numbered("Nested eight", 1),
                numbered("Eight"),
            ],
            extras={"word/numbering.xml": numbering_xml()},
        )
        _pipeline.convert_docx(self.source, split_depth=1)
        content = (self.root / "Synthetic Guide" / "01-numbers.md").read_text(encoding="utf-8")
        shape = _recovery._list_shape(_reference.pandoc_html(content, self.pandoc))
        self.assertEqual(shape, [("ol", "1", 7, 2), ("ol", "1", 7, 2)])

    def test_unordered_word_lists_are_dash_markdown(self) -> None:
        write_docx(
            self.source,
            [numbered("First"), numbered("Nested", 1), numbered("Last")],
            extras={"word/numbering.xml": numbering_xml("bullet", 1)},
        )
        _pipeline.convert_docx(self.source)
        content = self.source.with_suffix(".md").read_text(encoding="utf-8")
        self.assertIn("- ", content)
        self.assertNotIn("* ", content)
        self.assertNotIn("+ ", content)
        self.assertEqual(
            _recovery._list_shape(_reference.pandoc_html(content, self.pandoc)),
            [("ul", "1", 1, 2), ("ul", "1", 1, 1)],
        )

    def test_nondecimal_ordered_list_becomes_a_numbered_markdown_list(self) -> None:
        # Markdown numbers lists only with decimals; Word's letters become numbers, not HTML.
        write_docx(
            self.source,
            [numbered("First"), numbered("Second")],
            extras={"word/numbering.xml": numbering_xml("lowerLetter", 3)},
        )
        _pipeline.convert_docx(self.source)
        content = self.source.with_suffix(".md").read_text(encoding="utf-8")
        self.assertNotIn("<", content)
        self.assertRegex(content, r"^3\.\s+First\n+4\.\s+Second\n")
        self.assertEqual(
            _recovery._list_shape(_reference.pandoc_html(content, self.pandoc)), [("ol", "1", 3, 2)]
        )

    def test_list_defined_by_a_word_list_style_is_a_list(self) -> None:
        # Pandoc ignores w:numStyleLink; the converter resolves it for both converters.
        result, report, warnings = self.convert_reported(
            [
                paragraph("Intro"),
                numbered("Alpha", number="2"),
                numbered("Beta", number="2"),
                paragraph("Outro"),
            ],
            styles=(word_style("Bullets", "numbering", number="1"),),
            extras={"word/numbering.xml": numbering_xml("bullet", 1, style_link="Bullets")},
        )
        self.assertEqual(report.status("lists"), "applied")
        self.assertRegex(result, r"Intro\n\n- Alpha\n+- Beta\n\nOutro")
        self.assertEqual(
            _recovery._list_shape(_reference.pandoc_html(result, self.pandoc)), [("ul", "1", 1, 2)]
        )
        self.assertNotIn("lists:", warnings)

    def test_inherited_style_numbering_replaces_plain_markitdown_paragraphs(self) -> None:
        # Mammoth gives paragraphs numbered through a basedOn style as plain paragraphs.
        result, report, warnings = self.convert_reported(
            [
                paragraph("Intro"),
                paragraph("Alpha", "Point"),
                paragraph("Beta", "Point"),
                paragraph("Outro"),
            ],
            styles=(
                word_style("ListBullet", number="1"),
                word_style("Point", based_on="ListBullet"),
            ),
            extras={"word/numbering.xml": numbering_xml("bullet", 1)},
        )
        self.assertEqual(report.status("lists"), "applied")
        self.assertRegex(result, r"Intro\n\n- Alpha\n+- Beta\n\nOutro")
        self.assertEqual(
            _recovery._list_shape(_reference.pandoc_html(result, self.pandoc)), [("ul", "1", 1, 2)]
        )
        self.assertNotIn("lists:", warnings)

    def test_paragraphs_without_a_defined_word_list_are_not_list_items(self) -> None:
        body = [paragraph("Intro"), numbered("Alpha", number="9"), numbered("Beta", number="9")]
        for name, extras in (
            ("undefined list", {"word/numbering.xml": numbering_xml("bullet", 1)}),
            ("no numbering part", None),
        ):
            with self.subTest(name):
                result, report, warnings = self.convert_reported(
                    body, output=self.root / f"{name}.md", extras=extras
                )
                self.assertEqual(report.status("lists"), "not needed")
                self.assertIn("Intro\n\nAlpha\n\nBeta", result)
                self.assertNotIn("lists:", warnings)

    def test_lists_after_fields_pandoc_cannot_close_keep_word_numbering(self) -> None:
        # Pandoc leaves such a field open and moves later list items' text out of the list.
        def marks(*kinds: str) -> list[ET.Element]:
            runs: list[ET.Element] = []
            for kind in kinds:
                run = ET.Element(W + "r")
                if kind in {"begin", "separate", "end"}:
                    ET.SubElement(run, W + "fldChar", {W + "fldCharType": kind})
                else:
                    ET.SubElement(run, W + "instrText").text = kind
                runs.append(run)
            return runs

        contents = hyperlink("Scope ", anchor="_Toc1")
        entry = contents.find(W + "hyperlink")
        assert entry is not None
        entry.extend(page_reference("_Toc1", "3"))
        for position, run in enumerate(marks("begin", ' TOC \\o "1-3" \\h ', "separate")):
            contents.insert(position, run)
        closing = paragraph()
        closing.extend(marks("end"))
        revision = paragraph("Revision ")
        revision.extend([*marks("begin", "separate"), _ooxml.word_run("2"), *marks("end")])
        caption = paragraph("Figure ")
        caption.extend(
            [*marks("begin", " SEQ Figure ", "separate"), _ooxml.word_run("1"), *marks("end")]
        )
        result, report, _warnings = self.convert_reported(
            [
                contents,
                closing,
                bookmark("_Toc1", "Scope", "Heading1"),
                revision,
                caption,
                paragraph("Intro"),
                numbered("Alpha"),
                numbered("Beta"),
                paragraph("Outro"),
            ],
            extras={"word/numbering.xml": numbering_xml()},
        )
        self.assertEqual(report.status("lists"), "applied")
        self.assertRegex(result, r"Intro\n\n7\.\s+Alpha\n+8\.\s+Beta\n\nOutro")
        for text in ("[Scope](#scope)", "Revision 2", "Figure 1"):
            self.assertIn(f"\n{text}\n", f"\n{result}")
        self.assertIn(
            "  fields: applied (2 field(s) unwrapped, 1 table-of-contents page number(s) dropped)",
            report.summary(),
        )

    def test_lists_that_start_at_a_nested_level_keep_word_numbering(self) -> None:
        # MarkItDown writes one marker per level for such a list, such as "* 1. Alpha".
        result, report, warnings = self.convert_reported(
            [paragraph("Intro"), numbered("Alpha", 1), numbered("Beta", 1), paragraph("Outro")],
            extras={"word/numbering.xml": numbering_xml()},
        )
        self.assertEqual(report.status("lists"), "applied")
        self.assertRegex(result, r"Intro\n\n7\.\s+Alpha\n+8\.\s+Beta\n\nOutro")
        self.assertNotIn("lists:", warnings)

    def test_consecutive_lists_at_a_nested_level_stay_separate_lists(self) -> None:
        # MarkItDown nests the second list in the first, four spaces in: "    1. B one".
        result, report, warnings = self.convert_reported(
            [
                paragraph("Intro"),
                numbered("A one", 2, number="2"),
                numbered("A two", 2, number="2"),
                numbered("B one", 2),
                numbered("B two", 2),
                paragraph("Outro"),
            ],
            extras={"word/numbering.xml": numbering_xml(second="bullet")},
        )
        self.assertEqual(report.status("lists"), "applied")
        self.assertRegex(
            result, r"Intro\n\n- A one\n+- A two\n\n7\.\s+B one\n+8\.\s+B two\n\nOutro"
        )
        parsed = _reference.pandoc_html(result, self.pandoc)
        self.assertEqual([node.tag for node in parsed], ["p", "ul", "ol", "p"])
        self.assertNotIn("lists:", warnings)

    def test_numbered_outline_level_heading_stays_a_list_item(self) -> None:
        # Pandoc and MarkItDown read it as a list item; only a "heading N" style is a heading.
        outlined = numbered("Gamma")
        properties = outlined.find(W + "pPr")
        assert properties is not None
        ET.SubElement(properties, W + "outlineLvl", {W + "val": "1"})
        result, report, warnings = self.convert_reported(
            [paragraph("Intro"), numbered("Alpha"), numbered("Beta"), outlined, paragraph("Outro")],
            extras={"word/numbering.xml": numbering_xml()},
        )
        self.assertEqual(report.status("lists"), "applied")
        self.assertRegex(result, r"Intro\n\n7\.\s+Alpha\n+8\.\s+Beta\n+9\.\s+Gamma\n\nOutro")
        self.assertIn(
            "    not applied (1): Word numbers this outline-level heading as a list item; "
            "kept as a list item",
            report.summary(),
        )
        self.assertEqual(report.status("cleanup"), "not needed")
        self.assertNotIn("lists:", warnings)

    def test_headings_lose_trailing_punctuation_that_list_items_keep(self) -> None:
        outlined = numbered("Gamma.")
        properties = outlined.find(W + "pPr")
        assert properties is not None
        ET.SubElement(properties, W + "outlineLvl", {W + "val": "1"})
        result, report, _warnings = self.convert_reported(
            [
                paragraph("Overview:", "Heading1"),
                paragraph("Why?", "Heading2"),
                bookmark("_Steps", "Steps.", "Heading2"),
                numbered("Alpha."),
                outlined,
                hyperlink("Back to the steps", anchor="_Steps"),
            ],
            extras={"word/numbering.xml": numbering_xml()},
        )
        self.assertIn("# Overview\n", result)
        self.assertIn("## Why?\n", result)
        self.assertIn("## Steps\n", result)
        self.assertRegex(result, r"7\.\s+Alpha\.\n+8\.\s+Gamma\.")
        self.assertIn("[Back to the steps](#steps)", result)
        self.assertIn(
            "  headings: partial (3 applied, 2 heading(s) with trailing punctuation trimmed, "
            "1 not applied)",
            report.summary(),
        )
        self.assertEqual(report.status("checks"), "applied")

    def test_skipped_heading_levels_close_with_their_subheadings(self) -> None:
        result, report, _warnings = self.convert_reported(
            [
                paragraph("Start", "Heading2"),
                paragraph("One", "Heading1"),
                paragraph("Three", "Heading3"),
                paragraph("Four", "Heading4"),
                paragraph("Three again", "Heading3"),
                paragraph("Two", "Heading2"),
                paragraph("Five", "Heading5"),
                table(row(cell(paragraph("In a cell", "Heading4"))), columns=1),
            ]
        )
        levels = re.findall(r"^(#+) (.+)$", result, re.MULTILINE)
        self.assertEqual(
            levels,
            [
                ("##", "Start"),
                ("#", "One"),
                ("##", "Three"),
                ("###", "Four"),
                ("##", "Three again"),
                ("##", "Two"),
                ("###", "Five"),
            ],
        )
        self.assertIn("<h4>In a cell</h4>", result)
        self.assertIn("4 heading(s) moved up to close a skipped level", "\n".join(report.summary()))
        self.assertEqual(report.status("checks"), "applied")

    def test_bare_web_and_e_mail_addresses_become_autolinks(self) -> None:
        result, report, _warnings = self.convert_reported(
            [
                paragraph("Owner: Jane <jane@example.com>; see https://example.com/spec."),
                hyperlink("https://linked.example", relation="site"),
                table(
                    row(cell("Page"), header=True),
                    row(cell("https://cell.example/p")),
                    # As Outlook writes a name and address, which the final check accepts.
                    row(cell("Reviewer <reviewer@cell.example>")),
                    columns=1,
                ),
            ],
            relations=(("site", "hyperlink", "https://linked.example", "External"),),
        )
        self.assertIn("Owner: Jane <jane@example.com>; see <https://example.com/spec>.", result)
        self.assertIn("<https://linked.example>", result)
        self.assertIn("| <https://cell.example/p> |", result)
        self.assertIn("| Reviewer <reviewer@cell.example> |", result)
        self.assertIn(
            "  links: applied (1, 4 bare web or e-mail address(es) linked)", report.summary()
        )
        self.assertEqual(report.status("checks"), "applied")

    def explained(
        self, words: str, name: str, *, explain: bool = True
    ) -> tuple[str, _diagnostics.TransformReport]:
        """A document with one item each transform skips; *words* is all of its text."""
        outlined = numbered(f"{words} outlined")
        properties = outlined.find(W + "pPr")
        assert properties is not None
        ET.SubElement(properties, W + "outlineLvl", {W + "val": "1"})
        # A footnote in a list item is a known gap: that list stays as MarkItDown converted it.
        noted = numbered(f"{words} noted")
        ET.SubElement(ET.SubElement(noted, W + "r"), W + "footnoteReference", {W + "id": "1"})
        notes = ET.Element(W + "footnotes")
        ET.SubElement(notes, W + "footnote", {W + "id": "1"}).append(paragraph(f"{words} note"))
        result, report, _warnings = self.convert_reported(
            [
                bookmark("_Toc1", f"{words} heading", "Heading1"),
                paragraph(f"{words} intro"),
                numbered(f"{words} one"),
                numbered(f"{words} two"),
                outlined,
                paragraph(f"{words} middle"),
                noted,
                numbered(f"{words} after"),
                hyperlink(f"{words} link", anchor="Zanzibar_spot"),
                hyperlink(f"{words} file", relation="guide"),
                text_box(f"{words} box"),
            ],
            output=self.root / f"{name}.md",
            relations=(
                ("notes", "footnotes", "footnotes.xml", "Internal"),
                ("guide", "hyperlink", "Xylophone%20Guide.docx", "External"),
            ),
            extras={
                "word/numbering.xml": numbering_xml(),
                "word/footnotes.xml": _ooxml.word_xml_bytes(notes),
            },
            explain=explain,
        )
        return result, report

    def test_explain_describes_what_was_not_applied_without_document_text(self) -> None:
        converted, report = self.explained("zephyr quill", "first")
        _other, other = self.explained("quokka marble", "second")
        lines = report.explanation()
        self.assertEqual(lines, other.explanation())
        for expected in (
            "docx-to-md: explain (fixed labels and counts only; no document text)",
            (
                "  document: paragraphs 11; text boxes 1; headings 2; list items 5; links 1; "
                "bookmarks 1"
            ),
            "  images: DrawingML text box: no embedded image; its text is omitted (1)",
            "    placement: floating 1",
            "  links: link to a missing Word bookmark; kept as plain text (1)",
            "    bookmark: nowhere 1",
            (
                "  headings: Word numbers this outline-level heading as a list item; "
                "kept as a list item (1)"
            ),
            "    headings: 1 of 2",
            "      heading by: outline level 1/1; heading style 0/1",
            (
                "  lists: Word/Pandoc/MarkItDown list content cannot be aligned; "
                "kept as MarkItDown converted it (1)"
            ),
            "    difference: differ at the middle: Pandoc letter / MarkItDown bracket 1",
            "    list items: 2 of 5",
            "      contains: text 1/4; text+note reference 1/1",
            "  checks: a local link target does not exist (1)",
            "    file type: docx 1",
            "    on disk: no 1",
        ):
            self.assertIn(expected, lines)
        shared = "\n".join(lines).casefold()
        for word in ("zephyr", "quill", "quokka", "marble", "zanzibar", "xylophone", "guide"):
            self.assertNotIn(word, shared)
        quiet, plain = self.explained("zephyr quill", "third", explain=False)
        self.assertEqual(quiet, converted)
        self.assertEqual(plain.summary(), report.summary())
        self.assertEqual((plain.explanation(), plain.items, plain.events), ([], {}, []))

    def test_explain_is_kept_in_the_diagnostic_report(self) -> None:
        write_docx(self.source, [hyperlink("Zephyr", anchor="Zanzibar_spot")])
        for explain in (True, False):
            with self.subTest(explain=explain):
                report = _diagnostics.TransformReport(explaining=explain)
                diagnostics = self.root / f"diagnostics-{explain}"
                with contextlib.redirect_stderr(io.StringIO()):
                    _pipeline.convert_docx(
                        self.source,
                        self.root / f"out-{explain}.md",
                        keep_intermediates=diagnostics,
                        report=report,
                    )
                data = json.loads((diagnostics / "diagnostics.json").read_text(encoding="utf-8"))
                if explain:
                    self.assertEqual(data["explain"], report.explanation())
                    self.assertIn("    bookmark: nowhere 1", data["explain"])
                else:
                    self.assertNotIn("explain", data)

    def test_numbering_off_paragraphs_stay_with_the_list_item_before_them(self) -> None:
        result, report, warnings = self.convert_reported(
            [
                paragraph("Intro"),
                numbered("Alpha"),
                numbered("More alpha", number="0"),
                numbered("Beta"),
                numbered("More beta", number="0"),
                paragraph("Outro"),
                numbered("Loose", number="0"),
            ],
            extras={"word/numbering.xml": numbering_xml()},
        )
        self.assertEqual(report.status("lists"), "applied")
        self.assertRegex(
            result, r"7\.\s+Alpha\n+ {4}More alpha\n+8\.\s+Beta\n+ {4}More beta\n\nOutro\n\nLoose"
        )
        self.assertNotIn("lists:", warnings)

    def test_paragraphs_pandoc_reads_as_headings_are_not_list_items(self) -> None:
        contents = word_style("TOCHeading", based_on="Heading1", number="1")
        properties = contents.find(W + "pPr")
        assert properties is not None
        ET.SubElement(properties, W + "outlineLvl", {W + "val": "9"})
        result, report, warnings = self.convert_reported(
            [
                paragraph("Contents", "TOCHeading"),
                paragraph("Intro"),
                numbered("Alpha"),
                paragraph("Outro"),
            ],
            styles=(contents,),
            extras={"word/numbering.xml": numbering_xml()},
        )
        self.assertEqual(report.status("lists"), "applied")
        self.assertIn("Contents", result)
        self.assertNotIn("lists:", warnings)

    def test_backticks_backslashes_and_hashes_keep_their_text(self) -> None:
        texts = (
            "Use `uvm_info and `uvm_error.",
            "# of entries",
            "Path C:\\tools\\a\\*b and \\\\server.",
        )
        items = ("Run `run_test and `uvm_fatal.", "Done")
        result, report, _warnings = self.convert_reported(
            [*(paragraph(text) for text in texts), *(numbered(item) for item in items)],
            extras={"word/numbering.xml": numbering_xml()},
        )
        parsed = _reference.pandoc_html(result, self.pandoc)

        def shown(tag: str) -> list[str]:
            return [" ".join("".join(node.itertext()).split()) for node in parsed.iter(tag)]

        self.assertEqual(shown("p")[: len(texts)], list(texts))
        self.assertEqual(shown("li"), list(items))
        self.assertFalse(list(parsed.iter("code")))
        self.assertFalse(list(parsed.iter("h1")))
        self.assertEqual(report.status("lists"), "applied")
        self.assertIn("  literal-text: applied (10)", report.summary())

    def test_brackets_stay_text_rather_than_links(self) -> None:
        result, report, _warnings = self.convert_reported(
            [
                paragraph("Set bit[3](W1C) and see [note]."),
                numbered("Clear field[7:0](RW) first."),
                numbered("Done"),
            ],
            extras={"word/numbering.xml": numbering_xml()},
        )
        parsed = _reference.pandoc_html(result, self.pandoc)
        self.assertEqual(_validation.collect_references(parsed).links, [])
        visible = "".join(parsed.itertext())
        for literal in ("Set bit[3](W1C) and see [note].", "Clear field[7:0](RW) first."):
            self.assertIn(literal, visible)
        self.assertEqual(report.status("lists"), "applied")
        self.assertEqual(report.status("checks"), "applied")

    def test_symbol_fonts_and_unclosable_emphasis_keep_lists(self) -> None:
        def styled(text: str, *, font: str | None = None, bold: bool = False) -> ET.Element:
            run = _ooxml.word_run(text)
            properties = ET.Element(W + "rPr")
            if bold:
                ET.SubElement(properties, W + "b")
            if font is not None:
                ET.SubElement(properties, W + "rFonts", {W + "ascii": font, W + "hAnsi": font})
            run.insert(0, properties)
            return run

        def line(*runs: ET.Element, item: bool = False) -> ET.Element:
            result = numbered("") if item else paragraph()
            for run in result.findall(W + "r"):
                result.remove(run)
            result.extend(runs)
            return result

        check = styled("\u00fc", font="Wingdings")
        result, report, warnings = self.convert_reported(
            [
                line(_ooxml.word_run("Passed "), check),
                line(_ooxml.word_run("Reviewed "), copy.deepcopy(check), item=True),
                line(styled("Note:", bold=True), _ooxml.word_run("next"), item=True),
                line(_ooxml.word_run("A "), styled("\u00ae", font="Symbol"), _ooxml.word_run(" B")),
            ],
            extras={"word/numbering.xml": numbering_xml()},
        )
        self.assertIn("Passed \u2713\n", result)
        self.assertIn("Reviewed \u2713", result)
        self.assertIn("A \u2192 B", result)
        self.assertNotIn("\u00fc", result)
        self.assertIn("<strong>Note:</strong>next", result)
        self.assertEqual(report.status("lists"), "applied")
        self.assertIn("  symbols: applied (3)", report.summary())
        self.assertNotIn("lists:", warnings)

    def test_vml_text_boxes_in_list_items_keep_the_list(self) -> None:
        # Pandoc splits a paragraph around a VML text box; MarkItDown puts its text after.
        item = numbered("Lead ")
        item.extend(list(text_box("Box label", legacy=True)))
        item.append(_ooxml.word_run(" tail"))
        result, report, _warnings = self.convert_reported(
            [paragraph("Intro"), item, numbered("Beta"), paragraph("Outro")],
            extras={"word/numbering.xml": numbering_xml()},
        )
        self.assertEqual(report.status("lists"), "applied")
        self.assertRegex(result, r"7\.\s+Lead tail\n\nBox label\n\n8\.\s+Beta\n")

    def test_lists_inside_tables_keep_word_numbers(self) -> None:
        write_docx(
            self.source,
            [
                table(
                    row(cell("Header"), header=True),
                    row(cell(numbered("Seven"), numbered("Eight"))),
                    columns=1,
                ),
            ],
            extras={"word/numbering.xml": numbering_xml()},
        )
        _pipeline.convert_docx(self.source)
        content = self.source.with_suffix(".md").read_text(encoding="utf-8")
        self.assertIn("| 7. Seven<br>8. Eight |", content)

    def test_multiple_empty_headerless_and_nested_tables(self) -> None:
        nested = table(row(cell("nested")), columns=1)
        result, stats = self.convert(
            [
                paragraph("Before"),
                table(
                    row(cell(""), cell("")), row(cell("data"), cell("")), row(cell(""), cell(""))
                ),
                table(),
                table(row(cell(""), cell(""))),
                table(row(cell("Outer", nested), cell("Tail"))),
                paragraph("After"),
            ]
        )
        parsed = _reference.pandoc_html(result, self.pandoc)
        tables = list(parsed.iter("table"))
        self.assertEqual(len(tables), 5)
        # The first table's empty rows go, so its data row leads a Markdown table.
        self.assertEqual([len(_tables.html_rows(item)) for item in tables], [1, 0, 1, 1, 1])
        self.assertEqual(stats["tables"], 5)
        self.assertEqual(len(list(parsed.iter("thead"))), 1)
        self.assertIn("nested", result)
        self.assertIn("Before", result)
        self.assertIn("After", result)

    def test_rows_with_grid_gaps_keep_their_word_cells(self) -> None:
        # Pandoc writes an empty cell per grid column for gridBefore and pads short rows.
        def gapped(before: int, after: int, *texts: str) -> ET.Element:
            result = row(*(cell(text) for text in texts))
            properties = ET.Element(W + "trPr")
            for name, value in (("gridBefore", before), ("gridAfter", after)):
                if value:
                    ET.SubElement(properties, W + name, {W + "val": str(value)})
            result.insert(0, properties)
            return result

        result, report, warnings = self.convert_reported(
            [
                table(
                    row(cell("a"), cell("b"), cell("c")),
                    gapped(2, 0, "z"),
                    gapped(0, 2, "x"),
                    gapped(0, 0, "p", "q"),
                    columns=3,
                )
            ]
        )
        self.assertEqual(report.status("tables"), "applied")
        self.assertNotIn("tables:", warnings)
        shape = [
            [(node.get("colspan", "1"), "".join(node.itertext())) for node in tr]
            for tr in _tables.html_rows(
                next(_reference.pandoc_html(result, self.pandoc).iter("table"))
            )
        ]
        self.assertEqual(
            shape,
            [
                [("1", "a"), ("1", "b"), ("1", "c")],
                [("2", ""), ("1", "z")],
                [("1", "x"), ("2", "")],
                [("1", "p"), ("1", "q")],
            ],
        )

    def test_rows_wider_than_the_table_grid_keep_every_cell(self) -> None:
        # Pandoc drops the cells beyond w:tblGrid, which Word tolerates.
        result, report, warnings = self.convert_reported(
            [
                table(
                    row(cell("a"), cell("b"), cell("c")),
                    row(cell("x"), cell("y"), cell("z")),
                    columns=2,
                )
            ]
        )
        self.assertEqual(report.status("tables"), "applied")
        self.assertNotIn("tables:", warnings)
        parsed = next(_reference.pandoc_html(result, self.pandoc).iter("table"))
        self.assertEqual(
            [["".join(node.itertext()) for node in tr] for tr in _tables.html_rows(parsed)],
            [["a", "b", "c"], ["x", "y", "z"]],
        )

    def test_a_narrow_first_body_row_keeps_every_table_cell(self) -> None:
        # Pandoc takes a table's columns from its first body row and drops the rest.
        title = row(cell("Title", colspan=2))
        result, report, warnings = self.convert_reported(
            [
                table(
                    row(cell("h1"), cell("h2"), cell("h3"), header=True),
                    row(cell("a"), cell("b")),
                    row(cell("x"), cell("y"), cell("z")),
                    columns=3,
                ),
                table(title, row(cell("p"), cell("q"), cell("r")), columns=3),
            ]
        )
        self.assertEqual(report.status("tables"), "applied")
        self.assertNotIn("tables:", warnings)
        shapes = [
            [
                [(node.get("colspan", "1"), "".join(node.itertext())) for node in tr]
                for tr in _tables.html_rows(rendered)
            ]
            for rendered in _reference.pandoc_html(result, self.pandoc).iter("table")
        ]
        self.assertEqual(
            shapes,
            [
                [
                    [("1", "h1"), ("1", "h2"), ("1", "h3")],
                    [("1", "a"), ("1", "b")],
                    [("1", "x"), ("1", "y"), ("1", "z")],
                ],
                [[("2", "Title")], [("1", "p"), ("1", "q"), ("1", "r")]],
            ],
        )

    def test_a_blank_header_row_goes_and_the_next_row_leads(self) -> None:
        result, _stats = self.convert(
            [
                table(
                    row(cell(""), cell(""), header=True),
                    row(cell("A | B"), cell("first", "", "second")),
                    row(cell(""), cell("")),
                ),
            ]
        )
        self.assertNotIn("<table", result)
        # The row that leads is the header, whose paragraphs become spaces.
        self.assertIn(r"| A \| B | first second |", result)
        parsed = _reference.pandoc_html(result, self.pandoc)
        actual = next(parsed.iter("table"))
        self.assertEqual(len(_tables.html_rows(actual)), 1)

    def test_nonempty_header_uses_editable_multiline_pipe_table(self) -> None:
        result, stats = self.convert(
            [
                table(
                    row(cell("Name"), cell("Details"), header=True),
                    row(cell("A | B"), cell("first", "", "second")),
                    row(cell(""), cell("")),
                ),
            ]
        )
        self.assertIn("| Name | Details |", result)
        self.assertIn("| --- | --- |", result)
        self.assertIn(r"A \| B", result)
        self.assertIn("first<br><br>second", result)
        self.assertEqual(stats["multiline_tables"], 1)
        self.assertEqual(len(list(_reference.pandoc_html(result, self.pandoc).iter("tr"))), 2)

    def test_aligned_word_cells_have_plain_separators_without_changing_literal_values(self) -> None:
        headers: list[ET.Element] = []
        for alignment in ("left", "center", "right"):
            heading = paragraph(alignment.title())
            props = ET.Element(W + "pPr")
            ET.SubElement(props, W + "jc", {W + "val": alignment})
            heading.insert(0, props)
            headers.append(cell(heading))
        result, _stats = self.convert(
            [
                table(
                    row(*headers, header=True),
                    row(cell(":---:"), cell("---:"), cell(":---")),
                    columns=3,
                )
            ]
        )
        self.assertIn("| Left | Center | Right |\n| --- | --- | --- |\n", result)
        self.assertIn("| :---: | ---: | :--- |\n", result)
        self.assertNotIn(
            "text-align", _reference.html_element(_reference.pandoc_html(result, self.pandoc))
        )

    def test_signal_identifiers_are_readable_in_prose_and_table_cells(self) -> None:
        result, _stats = self.convert(
            [
                paragraph("Signal _ctl_s_ready is low."),
                table(row(cell("Signal")), row(cell("_ctl_s_ready")), columns=1),
            ]
        )
        self.assertIn("Signal _ctl_s_ready is low.", result)
        self.assertIn("| _ctl_s_ready |", result)
        self.assertNotIn(r"\_ctl_s_ready", result)

    def test_an_all_empty_table_keeps_its_first_row_after_splitting(self) -> None:
        self.convert(
            [
                paragraph("Tables.", "Heading1"),
                table(row(cell(""), cell("")), row(cell(""), cell("")), row(cell(""), cell(""))),
            ],
            split_depth=6,
        )
        content = (self.root / "Synthetic Guide" / "01-tables.md").read_text(encoding="utf-8")
        self.assertEqual(len(list(_reference.pandoc_html(content, self.pandoc).iter("tr"))), 1)

    def test_zero_cell_rows_survive_actual_converter_streams(self) -> None:
        result, _stats = self.convert(
            [
                table(row(), row(cell("A"), cell("B")), row()),
                paragraph("Between"),
                table(row(), row()),
            ]
        )
        tables = _reference.pandoc_html(result, self.pandoc).iter("table")
        # Rows without cells show nothing, so they go unless no other row would be left.
        self.assertEqual(
            [[len(item) for item in _tables.html_rows(actual)] for actual in tables], [[2], [0]]
        )

    def test_empty_rows_are_omitted_and_reported(self) -> None:
        marked = row(cell(bookmark("_RowMark", "")), cell(""))
        result, report, _warnings = self.convert_reported(
            [
                paragraph("Tables", "Heading1"),
                table(
                    row(cell(""), cell("")),
                    row(cell("Name"), cell("Value")),
                    row(cell(""), cell("")),
                    row(cell("a"), cell("1")),
                    marked,
                ),
                table(
                    row(cell("Field"), cell("Notes")),
                    row(cell(""), cell("")),
                    row(cell(""), cell("")),
                ),
                hyperlink("Back to the row", anchor="_RowMark"),
            ]
        )
        self.assertIn("| Name | Value |\n| --- | --- |\n| a | 1 |\n", result)
        # A template's fill-in row stays.
        self.assertIn("| Field | Notes |\n| --- | --- |\n| | |\n", result)
        self.assertNotIn("<table", result)
        self.assertIn("[Back to the row](#tables)", result)
        self.assertIn("  tables: applied (2 Markdown, 4 empty row(s) omitted)", report.summary())
        self.assertEqual(report.status("checks"), "applied")

    def test_legacy_horizontal_merge_is_source_aware(self) -> None:
        first, continuation = cell("Merged"), cell("")
        first_props = first.find(W + "tcPr")
        continuation_props = continuation.find(W + "tcPr")
        assert first_props is not None and continuation_props is not None
        ET.SubElement(first_props, W + "hMerge", {W + "val": "restart"})
        ET.SubElement(continuation_props, W + "hMerge")
        result, _stats = self.convert(
            [
                table(row(first, continuation, header=True), row(cell("left"), cell("right"))),
            ]
        )
        self.assertIn('colspan="2"', result)
        self.assertEqual(len(list(_reference.pandoc_html(result, self.pandoc).iter("tr"))), 2)

    def test_merged_rows_and_source_caption_survive(self) -> None:
        result, _stats = self.convert(
            [
                table(
                    row(cell("Header", colspan=2), header=True),
                    row(cell("Shared", merge="restart"), cell("one")),
                    row(cell("", merge="continue"), cell("two")),
                    caption="A source caption",
                ),
            ]
        )
        self.assertIn('colspan="2"', result)
        self.assertIn('rowspan="2"', result)
        self.assertIn("<caption>A source caption</caption>", result)
        self.assertEqual(len(list(_reference.pandoc_html(result, self.pandoc).iter("tr"))), 3)

    def test_bookmark_and_parenthesized_identity(self) -> None:
        result, stats = self.convert(
            [
                bookmark("_Ref_(detail)", "Destination.", "Heading3"),
                hyperlink("See it", anchor="_Ref_(detail)"),
            ]
        )
        self.assertNotIn("<a ", result)
        self.assertIn("[See it](#destination)", result)
        self.assertIn("### Destination\n", result)
        self.assertEqual(stats["bookmarks"], 1)

    def test_bookmark_matching_its_heading_id_needs_no_html_workaround(self) -> None:
        result, _stats = self.convert(
            [
                bookmark("title", "Title", "Heading1"),
                hyperlink("Go", anchor="title"),
            ],
            split_depth=1,
        )
        self.assertIn("Title", result)
        child = (self.root / "Synthetic Guide" / "01-title.md").read_text(encoding="utf-8")
        parsed = _validation.collect_references(_reference.pandoc_html(child, self.pandoc))
        self.assertEqual(parsed.anchors.count("title"), 1)
        self.assertIn("# Title", child)
        self.assertIn("[Go](#title)", child)
        self.assertNotIn("<h1", child)
        self.assertNotIn("<a ", child)

    def test_word_bookmark_wins_over_a_different_heading_id(self) -> None:
        result, _stats = self.convert(
            [
                paragraph("Title", "Heading1"),
                hyperlink("Real destination", anchor="title"),
                bookmark("title", "Actual target", "Heading1"),
            ]
        )
        self.assertIn("[Real destination](#actual-target)", result)
        self.assertNotIn("<a ", result)
        self.assertEqual(
            _validation.collect_references(_reference.pandoc_html(result, self.pandoc)).anchors,
            ["title", "actual-target"],
        )

    def test_missing_word_bookmark_is_not_replaced_by_matching_heading_slug(self) -> None:
        result, report, warnings = self.convert_reported(
            [
                paragraph("Title", "Heading1"),
                hyperlink("Missing real bookmark", anchor="title"),
            ]
        )
        self.assertIn("Missing real bookmark", result)
        self.assertNotIn("](#title)", result)
        self.assertEqual(report.status("links"), "not applied")
        self.assertIn("link to missing Word bookmark 'title'; kept as plain text", warnings)

    def test_unicode_bookmark_and_output_name(self) -> None:
        output = self.root / "R\u00e9sum\u00e9.v2.md"
        result, _stats = self.convert(
            [
                bookmark("_R\u00e9sum\u00e9", "R\u00e9sum\u00e9. 1", "Heading3"),
                hyperlink("Go", anchor="_R\u00e9sum\u00e9"),
                image(),
            ],
            output=output,
            relations=(("image", "image", "media/image.png", "Internal"),),
            media={"image.png": PNG},
        )
        self.assertNotIn("<a ", result)
        self.assertIn("#r%C3%A9sum%C3%A9-1", result)
        self.assertIn(
            "r\u00e9sum\u00e9-1",
            _validation.collect_references(_reference.pandoc_html(result, self.pandoc)).anchors,
        )
        self.assertTrue((self.root / ".images" / "R\u00e9sum\u00e9.v2" / "image.png").is_file())

    def test_bookmark_in_table_and_table_link(self) -> None:
        result, _stats = self.convert(
            [
                bookmark("_Top", "Title.", "Heading1"),
                table(
                    row(cell("Header"), header=True),
                    row(cell(bookmark("_Cell", "Target"), hyperlink("Back", anchor="_Top"))),
                    columns=1,
                ),
                hyperlink("Cell", anchor="_Cell"),
            ]
        )
        self.assertNotIn("<a ", result)
        self.assertNotIn("<table", result)
        self.assertIn("| Header |", result)
        self.assertIn("[Cell](#title)", result)
        self.assertIn("[Back](#title)", result)

    def test_complex_table_bookmarks_use_section_links_without_anchor_wrappers(self) -> None:
        result, _stats = self.convert(
            [
                paragraph("1. Overview", "Heading1"),
                table(
                    row(cell(bookmark("_Cell", "Merged target"), colspan=2)),
                    row(cell(hyperlink("Return", anchor="_Cell")), cell("Data")),
                ),
                hyperlink("Go to table", anchor="_Cell"),
            ]
        )
        self.assertIn("<table", result)
        self.assertIn('href="#1-overview"', result)
        self.assertIn("[Go to table](#1-overview)", result)
        self.assertNotIn('id="_Cell"', result)
        self.assertNotIn('name="_Cell"', result)
        self.assertNotIn("<span", result)

    def test_preamble_bookmark_targets_document_top_across_split_pages(self) -> None:
        for depth in (0, 1):
            with self.subTest(depth=depth), contextlib.redirect_stderr(io.StringIO()):
                output = self.root / f"preamble-{depth}" / "guide.md"
                result, _stats = self.convert(
                    [
                        bookmark("_Before", "Opening text"),
                        hyperlink("Opening", anchor="_Before"),
                        paragraph("Body", "Heading1"),
                        hyperlink("Top", anchor="_Before"),
                    ],
                    output=output,
                    split_depth=depth,
                )
                self.assertIn("[Opening](#)", result)
                self.assertNotIn("<a ", result)
                if depth:
                    child = (output.parent / "guide" / "01-body.md").read_text(encoding="utf-8")
                    self.assertIn("[Top](../guide.md)", child)
                else:
                    self.assertIn("[Top](#)", result)
                    self.assertTrue(result.startswith("Opening text"))

    def test_only_referenced_nonheading_bookmarks_report_retargeting(self) -> None:
        write_docx(
            self.source,
            [
                bookmark("_Before", "Preamble"),
                bookmark("_Exact", "1. Overview", "Heading1"),
                bookmark("_Body", "Body target"),
                bookmark("_Unused", "No incoming links"),
                hyperlink("Preamble", anchor="_Before"),
                hyperlink("Section", anchor="_Exact"),
                hyperlink("Body", anchor="_Body"),
            ],
        )
        diagnostics = self.root / "diagnostics"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            _pipeline.convert_docx(self.source, keep_intermediates=diagnostics)
        warnings = stderr.getvalue()
        self.assertIn("'_Before' now links to the document top", warnings)
        self.assertIn("'_Body' now links to its containing section #1-overview", warnings)
        self.assertNotIn("_Unused", warnings)
        self.assertNotIn("_Exact", warnings)
        report = json.loads((diagnostics / "diagnostics.json").read_text(encoding="utf-8"))
        self.assertEqual(report["bookmark_targets"]["_Exact"]["kind"], "heading")
        self.assertEqual(report["bookmark_targets"]["_Body"]["kind"], "section")
        self.assertEqual(report["bookmark_targets"]["_Before"]["kind"], "document_top")

    def test_duplicate_heading_links_use_final_page_local_ids(self) -> None:
        for depth in (0, 1, 2):
            with self.subTest(depth=depth):
                output = self.root / f"duplicates-{depth}" / "guide.md"
                result, _stats = self.convert(
                    [
                        paragraph("First", "Heading1"),
                        bookmark("_One", "2. Same & Detail", "Heading2"),
                        hyperlink("Second target", anchor="_Two"),
                        paragraph("Second", "Heading1"),
                        bookmark("_Two", "2. Same & Detail", "Heading2"),
                        hyperlink("First target", anchor="_One"),
                    ],
                    output=output,
                    split_depth=depth,
                )
                if depth == 0:
                    self.assertIn("[Second target](#2-same--detail-1)", result)
                    self.assertIn("[First target](#2-same--detail)", result)
                elif depth == 1:
                    first = (output.parent / "guide" / "01-first.md").read_text(encoding="utf-8")
                    second = (output.parent / "guide" / "02-second.md").read_text(encoding="utf-8")
                    self.assertIn("[Second target](02-second.md#2-same--detail)", first)
                    self.assertIn("[First target](01-first.md#2-same--detail)", second)
                else:
                    first = (
                        output.parent / "guide" / "01-first" / "01-2-same-detail.md"
                    ).read_text(encoding="utf-8")
                    self.assertIn(
                        "[Second target](../02-second/01-2-same-detail.md#2-same--detail)", first
                    )

    def test_markdown_caption_does_not_change_word_section_mapping(self) -> None:
        result, _stats = self.convert(
            [
                bookmark("_Heading", "Heading", "Heading1"),
                table(row(cell("Header")), columns=1, caption="# Not a heading"),
                hyperlink("Actual section", anchor="_Heading"),
            ]
        )
        self.assertNotIn("<table", result)
        parsed = _reference.pandoc_html(result, self.pandoc)
        self.assertEqual(_validation.collect_references(parsed).headings, [1])
        self.assertIn("# Not a heading", "".join(parsed.itertext()))
        self.assertIn("[Actual section](#heading)", result)

    def test_link_to_missing_bookmark_is_kept_as_text_and_summarized(self) -> None:
        output = self.root / "output.md"
        output.write_text("old", encoding="utf-8")
        result, report, _warnings = self.convert_reported(
            [hyperlink("Missing", anchor="_Missing")], output=output
        )
        self.assertEqual(result, "Missing\n")
        summary = report.summary()
        self.assertEqual(summary[0], "docx-to-md: transforms (baseline: MarkItDown)")
        self.assertIn("  alignment: applied", summary)
        self.assertIn("  links: not applied (1)", summary)
        self.assertIn(
            "    not applied (1): link to a missing Word bookmark; kept as plain text", summary
        )
        self.assertIn("  checks: passed", summary)
        self.assertNotIn("_Missing", "\n".join(summary))

    def test_cross_references_to_missing_bookmarks_are_kept_as_text(self) -> None:
        def reference(anchor: str, value: str, *, simple: bool = False) -> ET.Element:
            result = paragraph("See ")
            result.extend(page_reference(anchor, value, simple=simple))
            return result

        result, report, warnings = self.convert_reported(
            [
                bookmark("_Target", "Target", "Heading1"),
                table(
                    row(cell(reference("_Missing", "3")), cell(reference("_Target", "4"))),
                    row(cell(reference("_Gone", "5", simple=True)), cell("Plain")),
                ),
                reference("_Missing", "6"),
            ]
        )
        self.assertIn("| See 3 | See [4](#target) |", result)
        self.assertIn("| See 5 | Plain |", result)
        self.assertIn("See 6", result)
        self.assertEqual(
            dict(report.records["links"].skipped),
            {"link to a missing Word bookmark; kept as plain text": 3},
        )
        self.assertIn(
            "links: cross-reference to missing Word bookmark '_Gone'; kept as plain text",
            warnings,
        )
        self.assertEqual(report.status("pages"), "applied")
        self.assertEqual(report.status("checks"), "applied")

    def test_simple_fields_keep_their_cached_results(self) -> None:
        def field(instruction: str, *children: ET.Element) -> ET.Element:
            result = ET.Element(W + "fldSimple", {W + "instr": instruction})
            result.extend(children)
            return result

        def line(*parts: str | ET.Element) -> ET.Element:
            result = ET.Element(W + "p")
            result.extend(
                _ooxml.word_run(part) if isinstance(part, str) else part for part in parts
            )
            return result

        def fail(*_arguments: object) -> NoReturn:
            raise _models.ConversionError("synthetic alignment failure")

        figure = field(
            " SEQ Figure \\* ARABIC ",
            ET.Element(W + "bookmarkStart", {W + "id": "5", W + "name": "_RefFigure"}),
            _ooxml.word_run("3"),
            ET.Element(W + "bookmarkEnd", {W + "id": "5"}),
        )
        body = [
            paragraph("Results", "Heading1"),
            line(
                "Table ",
                field(" STYLEREF 1 \\s ", _ooxml.word_run("2")),
                "\u2011",
                field(" SEQ Table \\* ARABIC \\s 1 ", _ooxml.word_run("1")),
                ": Registers",
            ),
            line("Figure ", figure, ": Pipeline"),
            line("See ", field(" REF _RefFigure \\h ", _ooxml.word_run("Figure 3")), "."),
            hyperlink("Back to the figure", anchor="_RefFigure"),
        ]
        for aligned in (True, False):
            with self.subTest(aligned=aligned), contextlib.ExitStack() as stack:
                if not aligned:
                    stack.enter_context(patch.object(_pipeline, "_aligned_markdown", fail))
                result, report, _warnings = self.convert_reported(
                    body, output=self.root / f"fields-{aligned}.md"
                )
                for text in ("Table 2\u20111: Registers", "Figure 3: Pipeline", "See Figure 3."):
                    self.assertIn(f"\n{text}\n", result)
                self.assertEqual(report.status("fields"), "applied")
                self.assertIn("  fields: applied (4 simple field(s) kept)", report.summary())
                if aligned:
                    self.assertIn("[Back to the figure](#results)", result)
                    self.assertFalse(report.records["bookmarks"].skipped)
                    self.assertEqual(report.status("checks"), "applied")
                    self.assertFalse(report.records["checks"].skipped)

    def test_bookmarks_in_leading_title_paragraphs_keep_their_position(self) -> None:
        # Pandoc reads leading Title and Subtitle paragraphs as metadata, outside its body.
        result, report, warnings = self.convert_reported(
            [
                paragraph(""),
                bookmark("_Hlk1", "Document title", "Title"),
                paragraph("Document subtitle", "Subtitle"),
                paragraph("Body"),
                hyperlink("Back to the title", anchor="_Hlk1"),
            ],
            styles=(word_style("Title"), word_style("Subtitle")),
        )
        self.assertEqual(report.status("checks"), "applied")
        self.assertFalse(report.records["checks"].skipped)
        self.assertEqual(report.status("bookmarks"), "applied")
        self.assertRegex(
            result, r"Document title\n\nDocument subtitle\n\nBody\n\n\[Back to the title\]\(#"
        )
        self.assertNotIn("checks:", warnings)

    def test_word_line_breaks_start_new_paragraphs(self) -> None:
        # MarkItDown strips the trailing spaces of its hard breaks, so Word's line breaks
        # are restored, as paragraph breaks; one in a heading stays a space.
        def broken(*parts: ET.Element | str, style: str | None = None) -> ET.Element:
            result = paragraph("", style)
            for run in result.findall(W + "r"):
                result.remove(run)
            for index, part in enumerate(parts):
                if index:
                    ET.SubElement(ET.SubElement(result, W + "r"), W + "br")
                if isinstance(part, str):
                    result.append(_ooxml.word_run(part))
                else:
                    result.extend(list(part))
            return result

        result, report, _warnings = self.convert_reported(
            [
                broken("first line", "second line"),
                broken("Heading top", "Heading bottom", style="Heading1"),
                broken(image("art"), hyperlink("Source", relation="site")),
            ],
            relations=(
                ("art", "image", "media/art.png", "Internal"),
                ("site", "hyperlink", "https://example.test/a?b=1&c=2", "External"),
            ),
            media={"art.png": PNG},
        )
        self.assertIn("first line\n\nsecond line\n", result)
        self.assertIn("# Heading top Heading bottom\n", result)
        self.assertRegex(
            result,
            r"!\[Diagram\]\(\.images/[^)\n]+\)\n\n\[Source\]\(https://example\.test/a\?b=1&amp;c=2\)",
        )
        self.assertNotIn("\\\n", result)
        self.assertIn("  line-breaks: partial (2 applied, 1 not applied)", report.summary())
        self.assertEqual(report.status("checks"), "applied")
        self.assertFalse(report.records["checks"].skipped)

    def test_line_breaks_at_block_ends_leave_no_backslash(self) -> None:
        def trailing(content: ET.Element) -> ET.Element:
            ET.SubElement(ET.SubElement(content, W + "r"), W + "br")
            return content

        result, report, warnings = self.convert_reported(
            [
                paragraph("Intro"),
                trailing(numbered("Alpha")),
                numbered("Beta"),
                trailing(paragraph("Plain")),
                trailing(bookmark("_Ref1", "Marked")),
                hyperlink("Back", anchor="_Ref1"),
            ],
            extras={"word/numbering.xml": numbering_xml()},
        )
        self.assertNotIn("\\", result)
        self.assertRegex(result, r"7\.\s+Alpha\n+8\.\s+Beta\n\nPlain\n\nMarked\n")
        self.assertEqual(report.status("lists"), "applied")
        self.assertNotIn("<ol", result)
        self.assertNotIn("lists:", warnings)

    def test_stray_field_marks_do_not_stop_markitdown(self) -> None:
        def fail(*_arguments: object) -> NoReturn:
            raise _models.ConversionError("synthetic alignment failure")

        stray = paragraph("Before")
        ET.SubElement(ET.SubElement(stray, W + "r"), W + "fldChar", {W + "fldCharType": "end"})
        for aligned in (True, False):
            with self.subTest(aligned=aligned), contextlib.ExitStack() as stack:
                if not aligned:
                    stack.enter_context(patch.object(_pipeline, "_aligned_markdown", fail))
                result, report, _warnings = self.convert_reported(
                    [stray, paragraph("After")], output=self.root / f"stray-{aligned}.md"
                )
                self.assertIn("Before\n\nAfter", result)
                self.assertIn("  fields: applied (1 stray field mark(s) removed)", report.summary())

    def test_nested_empty_and_targetless_hyperlinks(self) -> None:
        nested = hyperlink("Outer ", relation="outer")
        outer = nested.find(W + "hyperlink")
        assert outer is not None
        ET.SubElement(outer, W + "hyperlink", {R + "id": "inner"}).append(_ooxml.word_run("Inner"))
        result, report, warnings = self.convert_reported(
            [nested, hyperlink(" ", relation="outer"), hyperlink("Plain")],
            relations=(
                ("outer", "hyperlink", "https://example.test/outer", "External"),
                ("inner", "hyperlink", "https://example.test/inner", "External"),
            ),
        )
        self.assertEqual(result, "[Outer Inner](https://example.test/outer)\n\nPlain\n")
        summary = report.summary()
        self.assertIn("  links: partial (1 applied, 1 not applied)", summary)
        self.assertIn(
            "    not applied (1): Word hyperlink nested in another hyperlink; "
            "its text stays in the outer link",
            summary,
        )
        self.assertEqual(warnings.count("links:"), 1)

    def test_hyperlink_with_a_missing_relationship_still_converts(self) -> None:
        def fail(*_arguments: object) -> NoReturn:
            raise _models.ConversionError("synthetic alignment failure")

        for aligned in (True, False):
            with self.subTest(aligned=aligned), contextlib.ExitStack() as stack:
                if not aligned:
                    stack.enter_context(patch.object(_pipeline, "_aligned_markdown", fail))
                result, report, warnings = self.convert_reported(
                    [paragraph("Before"), hyperlink("Dangling", relation="gone")],
                    output=self.root / f"dangling-{aligned}.md",
                )
                self.assertEqual(result, "Before\n\nDangling\n")
                self.assertEqual(report.records["alignment"].failure is None, aligned)
                self.assertEqual(
                    dict(report.records["links"].skipped),
                    {"Word hyperlink relationship is missing; its target is dropped": 1},
                )
                self.assertIn(
                    "links: Word hyperlink relationship 'gone' is missing; its target is dropped",
                    warnings,
                )

    def test_hyperlink_that_pandoc_splits_keeps_the_markitdown_link(self) -> None:
        content = hyperlink("Label", relation="site")
        link = content.find(W + "hyperlink")
        assert link is not None
        ET.SubElement(ET.SubElement(link, W + "r"), W + "footnoteReference", {W + "id": "1"})
        notes = (
            f'<w:footnotes xmlns:w="{W[1:-1]}"><w:footnote w:id="1"><w:p><w:r>'
            "<w:t>Note</w:t></w:r></w:p></w:footnote></w:footnotes>"
        )
        result, report, _warnings = self.convert_reported(
            [content],
            relations=(
                ("site", "hyperlink", "https://example.test/a", "External"),
                ("notes", "footnotes", "footnotes.xml", "Internal"),
            ),
            extras={"word/footnotes.xml": notes.encode()},
        )
        self.assertIn("https://example.test/a", result)
        self.assertEqual(
            dict(report.records["links"].skipped),
            {"Pandoc rendered N links for one Word hyperlink; kept as MarkItDown converted it": 1},
        )

    def test_png_and_svg_do_not_require_office_or_poppler(self) -> None:
        original = shutil.which

        def forbid_optional(command: str) -> str | None:
            if command in {"libreoffice", "soffice", "pdftocairo"}:
                self.fail(f"Unnecessary optional dependency probe: {command}")
            return original(command)

        with patch.object(shutil, "which", forbid_optional):
            result, stats = self.convert(
                [image("png"), image("svg")],
                relations=(
                    ("png", "image", "media/image.png", "Internal"),
                    ("svg", "image", "media/image.svg", "Internal"),
                ),
                media={"image.png": PNG, "image.svg": SVG},
            )
        self.assertIn(".images/Synthetic%20Guide/image.png", result)
        self.assertIn(".images/Synthetic%20Guide/image.svg", result)
        self.assertEqual(stats["image_occurrences"], 2)
        self.assertEqual(
            (self.root / ".images" / "Synthetic Guide" / "image.png").read_bytes(), PNG
        )

    def test_line_breaks_in_list_items_formatting_and_links(self) -> None:
        item = numbered("Alpha")
        ET.SubElement(ET.SubElement(item, W + "r"), W + "br")
        item.append(_ooxml.word_run("continued"))
        bold = paragraph("Bold", bold=True)
        for child in ("br", "t"):
            run = ET.SubElement(bold, W + "r")
            ET.SubElement(ET.SubElement(run, W + "rPr"), W + "b")
            ET.SubElement(run, W + child).text = "text" if child == "t" else None
        link = hyperlink("Line one", relation="site")
        anchor = link.find(W + "hyperlink")
        assert anchor is not None
        ET.SubElement(ET.SubElement(anchor, W + "r"), W + "br")
        anchor.append(_ooxml.word_run("Line two"))
        result, report, _warnings = self.convert_reported(
            [item, numbered("Beta"), bold, link],
            relations=(("site", "hyperlink", "https://example.test/", "External"),),
            extras={"word/numbering.xml": numbering_xml()},
        )
        self.assertEqual(
            result,
            "7. Alpha\n\n    continued\n\n8. Beta\n\n**Bold**\n\n**text**\n\n"
            "[Line one Line two](https://example.test/)\n",
        )
        self.assertEqual(report.status("lists"), "applied")
        self.assertEqual(report.status("checks"), "applied")

    def test_list_markers_take_one_space_and_lines_have_no_trailing_whitespace(self) -> None:
        result, report, _warnings = self.convert_reported(
            [
                hyperlink("Go", anchor="_Toc1"),
                bookmark("_Toc1", "Scope\u00a0", "Heading1"),
                paragraph("Text ends with spaces\u00a0 "),
                numbered("Alpha\u00a0"),
                numbered(""),
                numbered("Beta"),
                paragraph("Outro"),
            ],
            extras={"word/numbering.xml": numbering_xml()},
        )
        self.assertEqual(
            result,
            "[Go](#scope)\n\n# Scope\n\nText ends with spaces\n\n"
            "7. Alpha\n\n8.\n\n9. Beta\n\nOutro\n",
        )
        self.assertIn("  spaces: applied (3)", report.summary())
        self.assertIn(
            "  cleanup: applied (1 line(s) with trailing whitespace trimmed)", report.summary()
        )
        self.assertEqual(report.status("checks"), "applied")

    def test_empty_headings_are_omitted_and_do_not_prevent_splitting(self) -> None:
        report = _diagnostics.TransformReport()
        with contextlib.redirect_stderr(io.StringIO()):
            self.convert(
                [
                    paragraph("One", "Heading1"),
                    paragraph("", "Heading2"),
                    paragraph("Text"),
                    paragraph("Two", "Heading1"),
                    paragraph("\u00a0", "Heading2"),
                    paragraph("End"),
                ],
                split_depth=1,
                qualify=True,
                report=report,
            )
        self.assertEqual(report.status("pages"), "applied")
        self.assertEqual(report.status("duplicate-headings"), "not needed")
        self.assertIn("    not applied (2): Word heading has no text; omitted", report.summary())
        pages = self.root / "Synthetic Guide"
        self.assertEqual((pages / "01-one.md").read_text(encoding="utf-8"), "# One\n\nText\n")
        self.assertEqual((pages / "02-two.md").read_text(encoding="utf-8"), "# Two\n\nEnd\n")

    def test_bookmark_on_an_empty_heading_goes_to_the_heading_after_it(self) -> None:
        # Enter at the start of a heading leaves its table-of-contents bookmark in the
        # empty heading above it.
        result, report, _warnings = self.convert_reported(
            [
                hyperlink("2.1 Interfaces", anchor="_Toc1"),
                hyperlink("Notes", anchor="_Ref2"),
                paragraph("Design", "Heading1"),
                paragraph("Text"),
                bookmark("_Toc1", "", "Heading2"),
                paragraph("", "Heading3"),
                paragraph("Interfaces", "Heading2"),
                paragraph("Ports"),
                bookmark("_Ref2", "", "Heading2"),
                paragraph("A note under Interfaces"),
            ]
        )
        self.assertEqual(
            result,
            "[2.1 Interfaces](#interfaces)\n\n[Notes](#interfaces)\n\n# Design\n\nText\n\n"
            "## Interfaces\n\nPorts\n\nA note under Interfaces\n",
        )
        self.assertIn("    not applied (3): Word heading has no text; omitted", report.summary())
        self.assertEqual(report.status("checks"), "applied")
        self.assertFalse(report.records["checks"].skipped)

    def test_bookmark_in_an_empty_paragraph_goes_to_the_heading_after_it(self) -> None:
        result, report, warnings = self.convert_reported(
            [
                hyperlink("Figure 1: Pipe", anchor="_Toc5"),
                paragraph("Previous", "Heading1"),
                paragraph("Text"),
                bookmark("_Toc5", ""),
                paragraph("Buffers", "Heading1"),
                paragraph("Figure 1: Pipe"),
            ]
        )
        self.assertIn("[Figure 1: Pipe](#buffers)", result)
        self.assertEqual(report.status("checks"), "applied")
        self.assertNotIn("_Toc5", warnings)

    def test_heading_with_only_a_picture_is_a_paragraph(self) -> None:
        picture = image()
        properties = ET.Element(W + "pPr")
        ET.SubElement(properties, W + "pStyle", {W + "val": "Heading3"})
        picture.insert(0, properties)
        picture.insert(1, ET.Element(W + "bookmarkStart", {W + "id": "9", W + "name": "_Toc9"}))
        picture.append(ET.Element(W + "bookmarkEnd", {W + "id": "9"}))
        result, report, _warnings = self.convert_reported(
            [
                hyperlink("Figure 1: Diagram", anchor="_Toc9"),
                paragraph("Block diagram", "Heading2"),
                picture,
                paragraph("Figure 1: Diagram"),
                paragraph("Next", "Heading2"),
            ],
            relations=(("image", "image", "media/image.png", "Internal"),),
            media={"image.png": PNG},
        )
        self.assertEqual(
            result,
            "[Figure 1: Diagram](#block-diagram)\n\n## Block diagram\n\n"
            "![Diagram](.images/Synthetic%20Guide/image.png)\n\nFigure 1: Diagram\n\n## Next\n",
        )
        self.assertIn(
            "    not applied (1): Word heading has only a picture; kept as a paragraph",
            report.summary(),
        )
        self.assertEqual(report.status("images"), "applied")
        self.assertEqual(report.status("checks"), "applied")

    def test_word_title_becomes_the_only_h1(self) -> None:
        result, report, _warnings = self.convert_reported(
            [
                paragraph("XYZ_TOP Guide #2", "Title"),
                paragraph("Date: today"),
                hyperlink("Go to setup", anchor="_Toc1"),
                paragraph("Intro", "Heading1"),
                paragraph("Text"),
                bookmark("_Toc1", "Setup", "Heading2"),
                paragraph("More"),
            ],
            styles=(word_style("Title"),),
        )
        self.assertEqual(
            result,
            "# XYZ_TOP Guide \\#2\n\nDate: today\n\n[Go to setup](#setup)\n\n## Intro\n\n"
            "Text\n\n### Setup\n\nMore\n",
        )
        self.assertIn("  title: applied (2 heading(s) moved down one level)", report.summary())
        self.assertEqual(report.status("checks"), "applied")

    def test_word_title_stays_a_paragraph_when_headings_cannot_move_down(self) -> None:
        for name, body, reason in (
            (
                "level 6",
                [paragraph(f"Level {level}", f"Heading{level}") for level in range(1, 7)],
                (
                    "a heading is at level 6, the deepest Markdown level, so headings cannot "
                    "move down; the title stays a paragraph"
                ),
            ),
            (
                "same text",
                [paragraph("Guide Title", "Heading1"), paragraph("Text")],
                "the title as a heading would change other heading IDs; it stays a paragraph",
            ),
        ):
            with self.subTest(name=name):
                result, report, warnings = self.convert_reported(
                    [paragraph("Guide Title", "Title"), *body],
                    output=self.root / name / "guide.md",
                    styles=(word_style("Title"),),
                )
                self.assertTrue(result.startswith("Guide Title\n\n# "))
                self.assertIn(f"    not applied (1): {reason}", report.summary())
                self.assertIn("Word title 'Guide Title'", warnings)

    def test_split_root_page_does_not_repeat_the_word_title(self) -> None:
        report = _diagnostics.TransformReport()
        with contextlib.redirect_stderr(io.StringIO()):
            self.convert(
                [
                    paragraph("Guide Title", "Title"),
                    paragraph("Date: today"),
                    paragraph("One", "Heading1"),
                    paragraph("Text"),
                ],
                split_depth=1,
                report=report,
                styles=(word_style("Title"),),
            )
        self.assertEqual(
            (self.root / "Synthetic Guide.md").read_text(encoding="utf-8"),
            "# Guide Title\n\nDate: today\n\n## Sections\n\n- [One](Synthetic%20Guide/01-one.md)\n",
        )
        self.assertIn("  title: applied (1 repeated title paragraph(s) removed)", report.summary())

    def test_images_in_table_cells_keep_a_markdown_table(self) -> None:
        result, report, _warnings = self.convert_reported(
            [table(row(cell("Diagram"), header=True), row(cell(image())), columns=1)],
            relations=(("image", "image", "media/image.png", "Internal"),),
            media={"image.png": PNG},
        )
        self.assertEqual(
            result, "| Diagram |\n| --- |\n| ![Diagram](.images/Synthetic%20Guide/image.png) |\n"
        )
        self.assertIn("  tables: applied (1 Markdown)", report.summary())

    def test_missing_alt_text_is_empty_and_reported(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result, stats = self.convert(
                [image(alt=None)],
                relations=(("image", "image", "media/image.png", "Internal"),),
                media={"image.png": PNG},
            )
        self.assertEqual(stats["unresolved_alt_text"], 1)
        self.assertIn("Unresolved alt text", stderr.getvalue())
        self.assertIn("![](.images/Synthetic%20Guide/image.png)", result)

    def test_a_document_without_images_gets_no_image_directory(self) -> None:
        output = self.root / "out" / "Guide.md"
        self.convert(
            [image(alt="Diagram")],
            output=output,
            relations=(("image", "image", "media/image.png", "Internal"),),
            media={"image.png": PNG},
        )
        self.assertTrue((output.parent / ".images" / "Guide" / "image.png").is_file())
        write_docx(self.source, [paragraph("Text only")])
        _pipeline.convert_docx(self.source, output, replace_generated=True)
        self.assertEqual(sorted(path.name for path in output.parent.iterdir()), ["Guide.md"])

    def test_caption_alt_text_is_recovered_without_invention(self) -> None:
        result, _stats = self.convert(
            [image(alt=None), paragraph("Figure 2-1: Request flow", "Caption")],
            relations=(("image", "image", "media/image.png", "Internal"),),
            media={"image.png": PNG},
        )
        self.assertEqual(
            next(_reference.pandoc_html(result, self.pandoc).iter("img")).get("alt"), "Request flow"
        )

    def test_caption_replaces_word_alt_text_and_is_reported(self) -> None:
        boxed = paragraph("Note")
        boxed.extend(list(text_box("Box label", legacy=True)))
        result, report, _warnings = self.convert_reported(
            [
                image(alt="Generated description"),
                paragraph("Figure 3: Request flow", "Caption"),
                boxed,
            ],
            relations=(("image", "image", "media/image.png", "Internal"),),
            media={"image.png": PNG},
        )
        self.assertIn("![Request flow](.images/", result)
        self.assertIn(
            "  images: partial (1 applied, 1 image(s) described by their caption, 1 not applied)",
            report.summary(),
        )

    def test_an_image_directly_below_a_heading_takes_its_text(self) -> None:
        result, report, warnings = self.convert_reported(
            [
                paragraph("Block diagram", "Heading1"),
                paragraph(""),
                image(alt=None),
                paragraph("Details", "Heading1"),
                paragraph("Body text"),
                image(alt=None),
            ],
            relations=(("image", "image", "media/image.png", "Internal"),),
            media={"image.png": PNG},
        )
        self.assertIn("![Block diagram](.images/", result)
        self.assertIn("![](.images/", result)
        self.assertIn(
            "  images: applied (2, 1 image(s) described by the heading above)", report.summary()
        )
        self.assertEqual(warnings.count("Unresolved alt text"), 1)

    def test_repeated_and_nested_media_keep_exact_occurrences(self) -> None:
        result, stats = self.convert(
            [image(), table(row(cell(image(), "Table picture")), columns=1), image()],
            relations=(("image", "image", "media/image.png", "Internal"),),
            media={"image.png": PNG},
        )
        self.assertEqual(len(list(_reference.pandoc_html(result, self.pandoc).iter("img"))), 3)
        self.assertEqual(stats["unique_images"], 1)

    def test_linked_image_with_literal_alt_characters_survives(self) -> None:
        link = hyperlink("", relation="external")
        hyperlink_node = link.find(W + "hyperlink")
        assert hyperlink_node is not None
        hyperlink_node.clear()
        hyperlink_node.set(R + "id", "external")
        for run in image(alt=r"A [B] | C \ D").findall(W + "r"):
            hyperlink_node.append(run)
        result, _stats = self.convert(
            [link],
            relations=(
                ("image", "image", "media/image.png", "Internal"),
                ("external", "hyperlink", "https://example.test/target", "External"),
            ),
            media={"image.png": PNG},
        )
        parsed = _validation.collect_references(_reference.pandoc_html(result, self.pandoc))
        self.assertEqual(parsed.links, ["https://example.test/target"])
        self.assertEqual(
            next(_reference.pandoc_html(result, self.pandoc).iter("img")).get("alt"),
            r"A [B] | C \ D",
        )

    def test_multiple_images_and_text_in_one_word_run_are_not_dropped(self) -> None:
        content = ET.Element(W + "p")
        run = ET.SubElement(content, W + "r")
        ET.SubElement(run, W + "t").text = "Before "
        for _ in range(2):
            drawing = image().find(f"./{W}r/{W}drawing")
            assert drawing is not None
            run.append(drawing)
        ET.SubElement(run, W + "t").text = " After"
        result, stats = self.convert(
            [content],
            relations=(("image", "image", "media/image.png", "Internal"),),
            media={"image.png": PNG},
        )
        self.assertIn("Before", result)
        self.assertIn("After", result)
        self.assertEqual(stats["image_occurrences"], 2)

    def test_native_svg_extension_is_preferred_over_raster_fallback(self) -> None:
        content = image("png")
        blip = content.find(f".//{_ooxml.DRAWING}blip")
        assert blip is not None
        extension = ET.SubElement(
            ET.SubElement(blip, _ooxml.DRAWING + "extLst"), _ooxml.DRAWING + "ext"
        )
        ET.SubElement(extension, _ooxml.SVG_DRAWING + "svgBlip", {R + "embed": "svg"})
        result, stats = self.convert(
            [content],
            relations=(
                ("png", "image", "media/fallback.png", "Internal"),
                ("svg", "image", "media/vector.svg", "Internal"),
            ),
            media={"fallback.png": PNG, "vector.svg": SVG},
        )
        self.assertIn("vector.svg", result)
        self.assertNotIn("fallback.png", result)
        self.assertEqual(stats["unique_images"], 1)

    def test_legacy_picture_and_object_previews_keep_metadata(self) -> None:
        for kind in ("pict", "object"):
            with self.subTest(kind=kind):
                result, stats = self.convert(
                    [legacy_image(kind)],
                    output=self.root / f"{kind}.md",
                    relations=(("image", "image", "media/image.png", "Internal"),),
                    media={"image.png": PNG},
                )
                self.assertEqual(stats["image_occurrences"], 1)
                self.assertEqual(result.count("image.png"), 1)
                parsed = _reference.pandoc_html(result, self.pandoc)
                self.assertEqual(next(parsed.iter("img")).get("alt"), "Legacy preview")

    def test_packaged_picture_fallback_is_used_for_unsupported_native_drawing(self) -> None:
        content = ET.Element(W + "p")
        alternate = ET.SubElement(ET.SubElement(content, W + "r"), _ooxml.MC + "AlternateContent")
        choice = ET.SubElement(alternate, _ooxml.MC + "Choice", {"Requires": "wpg"})
        ET.SubElement(choice, W + "drawing")
        fallback = ET.SubElement(alternate, _ooxml.MC + "Fallback")
        picture = legacy_image().find(f"./{W}r/{W}pict")
        assert picture is not None
        fallback.append(picture)
        result, stats = self.convert(
            [content],
            relations=(("image", "image", "media/image.png", "Internal"),),
            media={"image.png": PNG},
        )
        self.assertEqual(stats["image_occurrences"], 1)
        self.assertIn("Legacy preview", result)

    def test_legacy_picture_with_a_text_box_keeps_each_once(self) -> None:
        content = legacy_image()
        picture = content.find(f"./{W}r/{W}pict")
        assert picture is not None
        caption = ET.SubElement(
            picture, _ooxml.VML + "shape", {"id": "caption", "type": "#_x0000_t202"}
        )
        box = ET.SubElement(ET.SubElement(caption, _ooxml.VML + "textbox"), _ooxml.TEXT_BOX_CONTENT)
        box.append(paragraph("Caption text"))
        result, stats = self.convert(
            [content],
            relations=(("image", "image", "media/image.png", "Internal"),),
            media={"image.png": PNG},
        )
        self.assertEqual(stats["image_occurrences"], 1)
        self.assertEqual(result.count("image.png"), 1)
        self.assertEqual(result.count("Caption text"), 1)

    def test_split_images_remain_relative_to_derived_namespace(self) -> None:
        self.convert(
            [paragraph("Chapter.", "Heading1"), paragraph("Deep.", "Heading3"), image()],
            split_depth=3,
            relations=(("image", "image", "media/image.png", "Internal"),),
            media={"image.png": PNG},
        )
        child = self.root / "Synthetic Guide" / "01-chapter" / "01-deep.md"
        self.assertIn(
            "../../.images/Synthetic%20Guide/image.png", child.read_text(encoding="utf-8")
        )

    def test_emf_svg_publication_collision_gets_a_distinct_name(self) -> None:
        def convert_emf(
            _data: bytes, destination: Path, _slot: Path, _findings: list[str], *, suffix: str
        ) -> None:
            self.assertEqual(suffix, ".emf")
            destination.write_bytes(SVG)

        with patch.object(_media, "_convert_emf", convert_emf):
            result, report, warnings = self.convert_reported(
                [image("emf"), image("svg")],
                relations=(
                    ("emf", "image", "media/image.emf", "Internal"),
                    ("svg", "image", "media/image.svg", "Internal"),
                ),
                media={"image.emf": synthetic_emf(), "image.svg": SVG},
            )
        self.assertIn("](.images/Synthetic%20Guide/image.svg)", result)
        self.assertIn("](.images/Synthetic%20Guide/image-2.svg)", result)
        self.assertIn("Image name image.svg is already used; published as image-2.svg", warnings)
        self.assertEqual(report.status("images"), "applied")
        self.assertEqual(report.status("checks"), "applied")

    def test_media_with_same_basename_in_distinct_package_paths_are_not_flattened(self) -> None:
        result, _stats = self.convert(
            [image("one"), image("two")],
            relations=(
                ("one", "image", "media/one/image.png", "Internal"),
                ("two", "image", "media/two/image.png", "Internal"),
            ),
            media={"one/image.png": PNG, "two/image.png": PNG},
        )
        self.assertIn("/one/image.png", result)
        self.assertIn("/two/image.png", result)

    def test_missing_media_is_dropped_and_reported_in_diagnostics(self) -> None:
        write_docx(
            self.source,
            [paragraph("Before"), image(), paragraph("After")],
            relations=(("image", "image", "media/missing.png", "Internal"),),
        )
        diagnostics = self.root / "diagnostics"
        report = _diagnostics.TransformReport()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            _pipeline.convert_docx(self.source, keep_intermediates=diagnostics, report=report)
        result = self.source.with_suffix(".md").read_text(encoding="utf-8")
        self.assertIn("Before", result)
        self.assertIn("After", result)
        self.assertNotIn("![", result)
        self.assertIn(
            "Missing Word media part 'word/media/missing.png'; image dropped", stderr.getvalue()
        )
        saved = json.loads((diagnostics / "diagnostics.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "published")
        self.assertEqual(saved["baseline"], "MarkItDown")
        self.assertEqual(saved["transforms"]["images"]["status"], "not applied")
        self.assertEqual(
            saved["transforms"]["images"]["not_applied"],
            {"Missing Word media part '...'; image dropped": 1},
        )

    def test_external_images_are_not_fetched_and_are_reported(self) -> None:
        result, report, warnings = self.convert_reported(
            [paragraph("Before"), image(), paragraph("After")],
            relations=(("image", "image", "https://example.test/image.png", "External"),),
        )
        self.assertIn("After", result)
        self.assertNotIn("![", result)
        self.assertNotIn("example.test", result)
        self.assertIn("not fetched: 'https://example.test/image.png'; image dropped", warnings)
        self.assertEqual(report.status("images"), "not applied")

    def test_linked_image_with_an_embedded_copy_uses_the_copy(self) -> None:
        content = image()
        blip = next(content.iter(_ooxml.DRAWING + "blip"))
        blip.set(R + "link", "linked")
        result, report, _warnings = self.convert_reported(
            [content],
            relations=(
                ("image", "image", "media/image.png", "Internal"),
                ("linked", "image", "cid:image001.png@01DA1234.5678ABCD", "External"),
            ),
            media={"image.png": PNG},
        )
        self.assertIn("](.images/Synthetic%20Guide/image.png)", result)
        self.assertNotIn("cid:", result)
        self.assertEqual(report.status("images"), "applied")
        self.assertEqual(report.status("checks"), "applied")

    def test_text_boxes_are_reported_without_stopping_conversion(self) -> None:
        result, report, warnings = self.convert_reported(
            [
                paragraph("Before"),
                text_box("Boxed drawing text"),
                text_box("Boxed legacy text", legacy=True),
                paragraph("After"),
            ]
        )
        self.assertIn("Before", result)
        self.assertIn("After", result)
        self.assertIn("Boxed legacy text", result)
        self.assertEqual(
            dict(report.records["images"].skipped),
            {
                "DrawingML text box: no embedded image; its text is omitted": 1,
                "VML text box: no embedded image; its text is kept after its paragraph": 1,
            },
        )
        self.assertIn("DrawingML text box at paragraph 2: no embedded image", warnings)

    def test_unconverted_vector_image_is_published_as_is(self) -> None:
        def fail(
            _data: bytes, _destination: Path, _slot: Path, _findings: list[str], *, suffix: str
        ) -> None:
            raise _models.ConversionError(f"synthetic {suffix} converter failure")

        with patch.object(_media, "_convert_emf", fail):
            result, report, warnings = self.convert_reported(
                [image("emf")],
                relations=(("emf", "image", "media/diagram.emf", "Internal"),),
                media={"diagram.emf": synthetic_emf()},
            )
        self.assertIn("](.images/Synthetic%20Guide/diagram.emf)", result)
        self.assertEqual(report.status("images"), "applied")
        self.assertEqual(
            dict(report.records["image-conversion"].skipped),
            {"EMF to SVG conversion failed; published as EMF": 1},
        )
        self.assertIn("diagram.emf: synthetic .emf converter failure", warnings)

    def test_wmf_drawings_are_converted_like_emf(self) -> None:
        def convert_wmf(
            _data: bytes, destination: Path, _slot: Path, _findings: list[str], *, suffix: str
        ) -> None:
            self.assertEqual(suffix, ".wmf")
            destination.write_bytes(SVG)

        with patch.object(_media, "_convert_emf", convert_wmf):
            result, report, _warnings = self.convert_reported(
                [image("wmf")],
                relations=(("wmf", "image", "media/diagram.wmf", "Internal"),),
                media={"diagram.wmf": b"synthetic WMF"},
            )
        self.assertIn("](.images/Synthetic%20Guide/diagram.svg)", result)
        self.assertEqual(dict(report.records["image-conversion"].applied), {"WMF to SVG": 1})
        self.assertEqual(report.status("images"), "applied")

    def test_picture_in_a_drawing_group_is_published(self) -> None:
        result, report, _warnings = self.convert_reported(
            [paragraph("Before"), grouped_image(), paragraph("After")],
            relations=(("image", "image", "media/grouped.png", "Internal"),),
            media={"grouped.png": PNG},
        )
        self.assertIn(".images/Synthetic%20Guide/grouped.png", result)
        self.assertIn("Grouped diagram", result)
        self.assertIn("After", result)
        self.assertEqual(report.status("images"), "applied")
        self.assertEqual(report.status("checks"), "applied")

    def test_dry_run_has_no_final_writes_and_retains_only_requested_bundle(self) -> None:
        write_docx(self.source, [paragraph("Title.", "Heading1")])
        output = self.root / "new" / "document.md"
        diagnostics = self.root / "diagnostics"
        _pipeline.convert_docx(
            self.source, output, split_depth=1, dry_run=True, keep_intermediates=diagnostics
        )
        self.assertFalse(output.parent.exists())
        self.assertEqual(
            json.loads((diagnostics / "diagnostics.json").read_text(encoding="utf-8"))["status"],
            "dry-run",
        )
        self.assertTrue((diagnostics / "markitdown.md").is_file())
        self.assertTrue((diagnostics / "pandoc.html").is_file())
        self.assertTrue((diagnostics / "reference.docx").is_file())

    def test_dry_run_without_diagnostics_leaves_directory_listing_unchanged(self) -> None:
        write_docx(self.source, [paragraph("Title.", "Heading1")])
        before = set(self.root.iterdir())
        _pipeline.convert_docx(self.source, dry_run=True)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_tmpdir_inside_images_is_not_used_as_scratch(self) -> None:
        write_docx(self.source, [paragraph("Text")])
        images = self.root / ".images" / self.source.stem
        images.mkdir(parents=True)
        original = _pipeline._convert_in_work

        def inspect_work(
            paths: _models.ConversionPaths,
            work: Path,
            *,
            qualify_duplicates: bool,
            dry_run: bool,
            report: _diagnostics.TransformReport | None = None,
        ) -> dict[str, int]:
            self.assertFalse(work.is_relative_to(images))
            return original(
                paths, work, qualify_duplicates=qualify_duplicates, dry_run=dry_run, report=report
            )

        with (
            patch.object(tempfile, "tempdir", str(images)),
            patch.object(_pipeline, "_convert_in_work", inspect_work),
        ):
            _pipeline.convert_docx(self.source, dry_run=True)
        self.assertFalse(any(images.iterdir()))
        self.assertFalse(self.source.with_suffix(".md").exists())

    def test_postpublication_diagnostic_failure_reports_outputs_are_published(self) -> None:
        write_docx(self.source, [paragraph("Published content")])
        original = _diagnostics.write_diagnostics
        diagnostics = self.root / "diagnostics"

        def fail_final_report(
            work: Path,
            paths: _models.ConversionPaths,
            findings: list[str],
            stats: dict[str, int],
            changes: list[_models.HeadingChange],
            status: str,
            error: str | None = None,
            *,
            source: _models.SourceDocument | None = None,
            report: _diagnostics.TransformReport | None = None,
        ) -> None:
            if status == "published":
                raise OSError("diagnostic completion failed")
            original(
                work, paths, findings, stats, changes, status, error, source=source, report=report
            )

        with (
            patch.object(_diagnostics, "write_diagnostics", fail_final_report),
            self.assertRaisesRegex(_models.ConversionError, "Outputs were published"),
        ):
            _pipeline.convert_docx(self.source, keep_intermediates=diagnostics)
        self.assertEqual(
            self.source.with_suffix(".md").read_text(encoding="utf-8"), "Published content\n"
        )
        report = json.loads((diagnostics / "diagnostics.json").read_text(encoding="utf-8"))
        self.assertIn("Outputs were published", report["error"])

    def test_dry_run_with_replacement_permission_preserves_existing_batch(self) -> None:
        write_docx(self.source, [paragraph("New", "Heading1")])
        output = self.source.with_suffix(".md")
        output.write_text("old markdown", encoding="utf-8")
        images = self.root / ".images" / "Synthetic Guide"
        images.mkdir(parents=True)
        (images / "old.png").write_bytes(PNG)
        pages = self.root / "Synthetic Guide"
        pages.mkdir()
        (pages / "old.md").write_text("old page", encoding="utf-8")
        _pipeline.convert_docx(self.source, split_depth=2, dry_run=True, replace_generated=True)
        self.assertEqual(output.read_text(encoding="utf-8"), "old markdown")
        self.assertEqual((images / "old.png").read_bytes(), PNG)
        self.assertEqual((pages / "old.md").read_text(encoding="utf-8"), "old page")

    def test_explicit_markdown_replacement_is_normal_without_directory_content(self) -> None:
        output = self.root / "selected.md"
        output.write_text("old", encoding="utf-8")
        result, _stats = self.convert([paragraph("new")], output=output)
        self.assertEqual(result, "new\n")

    def test_unsafe_destination_is_rejected_before_conversion_or_diagnostics_creation(self) -> None:
        write_docx(self.source, [paragraph("Text")])
        output = self.root / "alias.md"
        os.link(self.source, output)
        diagnostics = self.root / "diagnostics"
        with (
            patch.object(_source, "prepare_source") as prepare,
            self.assertRaises(_models.ConversionError),
        ):
            _pipeline.convert_docx(self.source, output, keep_intermediates=diagnostics)
        prepare.assert_not_called()
        self.assertFalse(diagnostics.exists())

    def test_hyperlink_field_tooltips_stay_out_of_links_without_pandoc(self) -> None:
        # mammoth keeps the \o switch in the link target; the fields become links first.
        simple = ET.Element(W + "p")
        field = ET.SubElement(
            simple,
            W + "fldSimple",
            {W + "instr": ' HYPERLINK "https://example.test/item" \\o "Tip" '},
        )
        field.append(_ooxml.word_run("Simple label"))
        complex_field = ET.Element(W + "p")
        for kind in ("begin", "instruction", "separate", "text", "end"):
            run = ET.SubElement(complex_field, W + "r")
            if kind == "instruction":
                instruction = ET.SubElement(run, W + "instrText")
                instruction.text = ' HYPERLINK "https://example.test/a" \\o "Tip" '
            elif kind == "text":
                ET.SubElement(run, W + "t").text = "Complex label"
            else:
                ET.SubElement(run, W + "fldChar", {W + "fldCharType": kind})
        write_docx(self.source, [simple, complex_field])
        original = shutil.which

        def without_pandoc(
            command: str, mode: int = os.F_OK | os.X_OK, path: str | None = None
        ) -> str | None:
            return None if command == "pandoc" else original(command, mode, path)

        report = _diagnostics.TransformReport()
        with (
            patch.object(shutil, "which", without_pandoc),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            _pipeline.convert_docx(self.source, report=report)
        self.assertEqual(
            self.source.with_suffix(".md").read_text(encoding="utf-8"),
            "[Simple label](https://example.test/item)\n\n"
            "[Complex label](https://example.test/a)\n",
        )
        self.assertEqual(report.status("cleanup"), "not needed")

    def test_missing_pandoc_publishes_the_markitdown_baseline(self) -> None:
        write_docx(
            self.source,
            [paragraph("Title", "Heading1"), paragraph("Text"), image()],
            relations=(("image", "image", "media/image.png", "Internal"),),
            media={"image.png": PNG},
        )
        original = shutil.which

        def without_pandoc(
            command: str, mode: int = os.F_OK | os.X_OK, path: str | None = None
        ) -> str | None:
            return None if command == "pandoc" else original(command, mode, path)

        report = _diagnostics.TransformReport()
        with (
            patch.object(shutil, "which", without_pandoc),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            stats = _pipeline.convert_docx(self.source, report=report)
        result = self.source.with_suffix(".md").read_text(encoding="utf-8")
        self.assertIn("# Title", result)
        self.assertIn("Text", result)
        self.assertIn("](.images/Synthetic%20Guide/image.png)", result)
        self.assertEqual(stats["image_occurrences"], 1)
        self.assertEqual(report.baseline_images, 1)
        unavailable = "Pandoc is not installed; see md-doc-converter doctor"
        for name in ("alignment", "images", "tables", "pages", "checks"):
            self.assertEqual(report.records[name].unavailable, unavailable, name)
        self.assertEqual(
            report.summary()[:2],
            [
                "docx-to-md: transforms (baseline: MarkItDown; 1 image(s) at MarkItDown positions)",
                f"  alignment: not applied: {unavailable}",
            ],
        )

    def test_hidden_text_is_dropped_with_and_without_alignment(self) -> None:
        def hidden(text: str) -> ET.Element:
            run = _ooxml.word_run(text)
            properties = ET.Element(W + "rPr")
            ET.SubElement(properties, W + "vanish")
            run.insert(0, properties)
            return run

        mixed = paragraph("Shown ")
        mixed.extend([hidden("SECRET "), _ooxml.word_run("text.")])
        whole = ET.Element(W + "p")
        whole.append(hidden("HIDDEN PARAGRAPH"))
        body = [paragraph("Title", "Heading1"), mixed, whole, paragraph("After")]
        result, report, _warnings = self.convert_reported(body)
        self.assertIn("Shown text.", result)
        self.assertNotIn("SECRET", result)
        self.assertNotIn("HIDDEN", result)
        self.assertIn("  hidden-text: applied (2)", report.summary())
        self.assertEqual(report.status("checks"), "applied")
        original = shutil.which

        def without_pandoc(
            command: str, mode: int = os.F_OK | os.X_OK, path: str | None = None
        ) -> str | None:
            return None if command == "pandoc" else original(command, mode, path)

        write_docx(self.source, body)
        report = _diagnostics.TransformReport()
        with (
            patch.object(shutil, "which", without_pandoc),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            _pipeline.convert_docx(self.source, replace_generated=True, report=report)
        result = self.source.with_suffix(".md").read_text(encoding="utf-8")
        self.assertIn("After", result)
        self.assertNotIn("SECRET", result)
        self.assertNotIn("HIDDEN", result)
        self.assertIn("  hidden-text: applied (2)", report.summary())

    def test_unicode_spaces_become_spaces_except_an_indent(self) -> None:
        body = [
            paragraph("Title", "Heading1"),
            paragraph("Clock 100\u00a0MHz and\u2002bit-bash\u200b."),
            paragraph("if (a) {"),
            paragraph("\u00a0 \u00a0 \u00a0 b = 1;\u00a0 // set"),
            paragraph("}"),
            table(
                row(cell("Name"), cell("Value"), header=True), row(cell("a\u202fb"), cell("\u00a0"))
            ),
            # Word alt text, as Office writes it, can hold a zero-width space.
            image(alt="\u200bDiagram\u00a0one"),
        ]
        relations = (("image", "image", "media/image.png", "Internal"),)
        media = {"image.png": PNG}
        result, report, warnings = self.convert_reported(body, relations=relations, media=media)
        self.assertIn("Clock 100 MHz and bit-bash.", result)
        # Markdown keeps only no-break spaces at the start of a paragraph.
        self.assertIn("if (a) {\n\n\u00a0\u00a0\u00a0\u00a0\u00a0\u00a0b = 1; // set\n\n}", result)
        self.assertIn("| a b | |", result)
        self.assertIn("![Diagram one](", result)
        self.assertEqual(result.count("\u00a0"), 6)
        self.assertIn("  spaces: applied (11)", report.summary())
        self.assertEqual(report.status("checks"), "applied", warnings)
        original = shutil.which

        def without_pandoc(
            command: str, mode: int = os.F_OK | os.X_OK, path: str | None = None
        ) -> str | None:
            return None if command == "pandoc" else original(command, mode, path)

        write_docx(self.source, body, relations=relations, media=media)
        report = _diagnostics.TransformReport()
        with (
            patch.object(shutil, "which", without_pandoc),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            _pipeline.convert_docx(self.source, replace_generated=True, report=report)
        result = self.source.with_suffix(".md").read_text(encoding="utf-8")
        self.assertIn("Clock 100 MHz and bit-bash.", result)
        self.assertIn("![Diagram one](", result)
        self.assertEqual(result.count("\u00a0"), 6)
        self.assertIn("  spaces: applied (11)", report.summary())

    def test_header_breaks_become_spaces_and_cells_lose_empty_edge_lines(self) -> None:
        def broken(first: str, second: str) -> ET.Element:
            run = _ooxml.word_run(first)
            ET.SubElement(run, W + "br")
            ET.SubElement(run, W + "t").text = second
            return run

        result, report, warnings = self.convert_reported(
            [
                paragraph("Title", "Heading1"),
                table(
                    row(
                        cell(paragraph_of(broken("Addr", "[31:24]"))),
                        cell("Two", "", "paras"),
                        header=True,
                    ),
                    row(cell(paragraph_of(broken("line1", "line2"))), cell("in", "", "side")),
                    row(cell("trailing", ""), cell("", "leading")),
                    row(cell(paragraph_of(broken("end", ""))), cell("x")),
                ),
            ]
        )
        self.assertIn(
            "| Addr [31:24] | Two paras |\n| --- | --- |\n| line1<br>line2 | in<br><br>side |\n"
            "| trailing | leading |\n| end | x |\n",
            result,
        )
        self.assertEqual(report.status("checks"), "applied", warnings)

    def test_monospace_paragraphs_become_a_fenced_code_block(self) -> None:
        indented = code_run("wait();")
        indented.insert(1, ET.Element(W + "tab"))
        blank = ET.Element(W + "p")
        mark = ET.SubElement(ET.SubElement(blank, W + "pPr"), W + "rPr")
        ET.SubElement(mark, W + "rFonts", {W + "ascii": "Consolas"})
        # A line break that starts the code leaves no empty first line.
        first = code_run("if (state == \u201cbusy\u201d) {")
        first.insert(1, ET.Element(W + "br"))
        body = [
            paragraph("Title", "Heading1"),
            paragraph("Before."),
            paragraph_of(first),
            paragraph_of(indented),
            blank,
            paragraph_of(code_run("} # end \u2013 done")),
            paragraph_of(code_run("```")),
            paragraph(),
            paragraph("After."),
        ]
        result, report, warnings = self.convert_reported(body)
        self.assertIn(
            'Before.\n\n```` text\nif (state == "busy") {\n    wait();\n\n'
            "} # end - done\n```\n````\n\nAfter.",
            result,
        )
        self.assertIn(
            "  code: applied (1 code block(s), 3 curly quote(s) and dash(es) straightened)",
            report.summary(),
        )
        self.assertEqual(report.status("checks"), "applied", warnings)

    def test_a_one_cell_monospace_table_becomes_a_code_block(self) -> None:
        listing = cell(
            paragraph_of(code_run("x = <a> & `b`")), paragraph(), paragraph_of(code_run("  y = 2"))
        )
        body = [
            paragraph("Title", "Heading1"),
            paragraph("Before."),
            table(row(listing), columns=1),
            paragraph("After."),
            table(row(cell(paragraph_of(code_run("not code"))), cell("Next")), columns=2),
        ]
        result, report, warnings = self.convert_reported(body)
        self.assertIn("Before.\n\n``` text\nx = <a> & `b`\n\n  y = 2\n```\n\nAfter.", result)
        self.assertIn("| `not code` | Next |", result)
        self.assertIn(
            "  code: applied (1 code block(s), 1 inline code span(s), "
            "1 one-cell table(s) made code blocks)",
            report.summary(),
        )
        self.assertEqual(report.status("checks"), "applied", warnings)

    def test_monospace_text_becomes_inline_code_outside_headings_links_and_fields(self) -> None:
        bold = code_run("reg[3:0] ")
        properties = bold.find(W + "rPr")
        assert properties is not None
        properties.insert(0, ET.Element(W + "b"))
        broken = code_run("one")
        ET.SubElement(broken, W + "br")
        ET.SubElement(broken, W + "t").text = "two"
        linked = hyperlink("linked label", relation="web")
        monospace = code_run("").find(W + "rPr")
        assert monospace is not None
        for run in linked.iter(W + "r"):
            run.insert(0, copy.deepcopy(monospace))
        field: list[ET.Element] = []
        for kind in ("begin", "instruction", "separate", "result", "end"):
            run = code_run("Field Author" if kind == "result" else "")
            if kind == "instruction":
                run.remove(run[1])
                ET.SubElement(run, W + "instrText").text = " AUTHOR "
            elif kind != "result":
                run.remove(run[1])
                ET.SubElement(run, W + "fldChar", {W + "fldCharType": kind})
            field.append(run)
        body = [
            paragraph("Title", "Heading1"),
            paragraph_of(
                _ooxml.word_run("Set "),
                code_run("ctrl_"),
                bold,
                _ooxml.word_run("to \u201cidle\u201d."),
            ),
            paragraph_of(_ooxml.word_run("Run "), code_run("`cmd`"), _ooxml.word_run(" now.")),
            paragraph_of(_ooxml.word_run("Broken "), broken, _ooxml.word_run(" end.")),
            linked,
            paragraph_of(_ooxml.word_run("By "), *field),
            paragraph_of(code_run("Monospace heading"), style="Heading2"),
            table(
                row(cell("Field"), cell("Value"), header=True),
                row(cell("mode"), cell(paragraph_of(code_run("a|b")))),
            ),
        ]
        result, report, warnings = self.convert_reported(
            body, relations=(("web", "hyperlink", "https://example.test/doc", "External"),)
        )
        self.assertIn("Set `ctrl_reg[3:0]` to \u201cidle\u201d.", result)
        self.assertIn("Run `` `cmd` `` now.", result)
        self.assertIn("Broken `one`\n\n`two` end.", result)
        self.assertIn("[linked label](https://example.test/doc)", result)
        self.assertIn("By Field Author", result)
        self.assertIn("## Monospace heading", result)
        self.assertIn("| mode | `a\\|b` |", result)
        self.assertIn("  code: applied (5 inline code span(s))", report.summary())
        self.assertEqual(report.status("checks"), "applied", warnings)

    def test_failed_alignment_falls_back_to_the_markitdown_baseline(self) -> None:
        def fail(*_arguments: object) -> NoReturn:
            raise _models.ConversionError("synthetic alignment failure")

        with patch.object(_pipeline, "_aligned_markdown", fail):
            result, report, warnings = self.convert_reported(
                [paragraph("Title", "Heading1"), image(), paragraph("Text")],
                relations=(("image", "image", "media/image.png", "Internal"),),
                media={"image.png": PNG},
            )
        self.assertIn("Text", result)
        self.assertIn("](.images/Synthetic%20Guide/image.png)", result)
        self.assertEqual(report.records["alignment"].failure, "synthetic alignment failure")
        self.assertEqual(report.records["tables"].unavailable, "alignment failed; see alignment")
        self.assertEqual(report.baseline_images, 1)
        self.assertEqual(report.status("checks"), "applied")
        self.assertIn(
            "alignment: failed and was not applied: synthetic alignment failure", warnings
        )

    def test_a_failing_layer_is_reported_and_the_rest_is_published(self) -> None:
        def fail(_markdown: str) -> tuple[str, int]:
            raise RuntimeError("synthetic layer failure")

        with patch.object(_normalization, "normalize_unordered_lists", fail):
            result, report, _warnings = self.convert_reported([paragraph("Text")])
        self.assertEqual(result, "Text\n")
        self.assertEqual(report.status("cleanup"), "not applied")
        self.assertEqual(report.records["cleanup"].failure, "RuntimeError: synthetic layer failure")
        self.assertEqual(report.status("checks"), "applied")

    def test_heading_level_seven_is_kept_as_a_paragraph(self) -> None:
        deep = paragraph("Level seven")
        properties = ET.Element(W + "pPr")
        ET.SubElement(properties, W + "outlineLvl", {W + "val": "6"})
        deep.insert(0, properties)
        result, report, _warnings = self.convert_reported([paragraph("Title", "Heading1"), deep])
        self.assertIn("Level seven", result)
        self.assertNotIn("# Level seven", result)
        self.assertEqual(
            dict(report.records["headings"].skipped),
            {"Word heading levels 7-9 have no Markdown equivalent; kept as a paragraph": 1},
        )

    def test_unnamed_and_duplicate_bookmarks_are_reported(self) -> None:
        result, report, _warnings = self.convert_reported(
            [
                bookmark("", "Unnamed target"),
                bookmark("_Twice", "First", "Heading1"),
                bookmark("_Twice", "Second", "Heading1"),
                hyperlink("Go", anchor="_Twice"),
            ]
        )
        self.assertIn("[Go](#first)", result)
        self.assertEqual(
            dict(report.records["bookmarks"].skipped),
            {
                "Word bookmark has no name; dropped": 1,
                "duplicate Word bookmark name; the later one is dropped": 1,
            },
        )

    def test_split_depths_follow_closed_heading_levels_and_bookmark_links(self) -> None:
        # Headings 1, 3, and 6 become levels 1, 2, and 3, and each depth splits at them.
        body = [
            paragraph("Preamble"),
            bookmark("_A", "First.", "Heading1"),
            hyperlink("Deeper", anchor="_B"),
            bookmark("_B", "Third.", "Heading3"),
            paragraph("Detail.", "Heading6"),
            hyperlink("Up", anchor="_A"),
        ]
        write_docx(self.source, body)
        for depth in range(7):
            with self.subTest(depth=depth):
                output = self.root / f"depth-{depth}" / "guide.md"
                stats = _pipeline.convert_docx(self.source, output, split_depth=depth)
                pages = output.parent / "guide"
                if depth == 0:
                    self.assertIn(
                        "# First\n\n[Deeper](#third)\n\n## Third\n\n### Detail\n\n[Up](#first)",
                        output.read_text(encoding="utf-8"),
                    )
                elif depth == 1:
                    self.assertIn(
                        "## Third\n\n### Detail\n\n[Up](#first)",
                        (pages / "01-first.md").read_text(encoding="utf-8"),
                    )
                elif depth == 2:
                    self.assertIn(
                        "[Deeper](01-first/01-third.md#third)",
                        (pages / "01-first.md").read_text(encoding="utf-8"),
                    )
                    self.assertIn(
                        "# Third\n\n## Detail\n\n[Up](../01-first.md#first)",
                        (pages / "01-first" / "01-third.md").read_text(encoding="utf-8"),
                    )
                else:
                    detail = pages / "01-first" / "01-third" / "01-detail.md"
                    self.assertIn(
                        "# Detail\n\n[Up](../../01-first.md#first)",
                        detail.read_text(encoding="utf-8"),
                    )
                self.assertGreaterEqual(stats["pages"], 1)

    def test_depth_zero_preserves_previous_page_tree(self) -> None:
        old = self.root / "Synthetic Guide"
        old.mkdir()
        (old / "old.md").write_text("old", encoding="utf-8")
        self.convert([paragraph("New text")])
        self.assertEqual((old / "old.md").read_text(encoding="utf-8"), "old")

    def test_duplicate_qualification_is_opt_in(self) -> None:
        result, stats = self.convert(
            [
                bookmark("_A", "Same.", "Heading3"),
                bookmark("_B", "Same.", "Heading3"),
                hyperlink("First", anchor="_A"),
                hyperlink("Second", anchor="_B"),
            ],
            qualify=True,
        )
        self.assertIn("### Same - occurrence 1", result)
        self.assertIn("### Same - occurrence 2", result)
        self.assertNotIn("<a ", result)
        self.assertIn("[First](#same---occurrence-1)", result)
        self.assertIn("[Second](#same---occurrence-2)", result)
        self.assertEqual(stats["qualified_headings"], 2)

    def test_generated_toc_results_are_retained(self) -> None:
        index = ET.Element(W + "p")
        field = ET.SubElement(index, W + "fldSimple", {W + "instr": 'TOC \\o "1-3" \\h'})
        field.append(_ooxml.word_run("Cached contents entry"))
        result, _stats = self.convert(
            [
                paragraph("Contents", "Heading1"),
                index,
                paragraph("Body", "Heading1"),
            ],
            split_depth=1,
        )
        self.assertIn("Contents", result)
        contents = self.root / "Synthetic Guide" / "01-contents.md"
        self.assertIn("Cached contents entry", contents.read_text(encoding="utf-8"))

    def test_complex_toc_field_results_and_blank_rows_survive(self) -> None:
        start = ET.Element(W + "p")
        run = ET.SubElement(start, W + "r")
        ET.SubElement(run, W + "fldChar", {W + "fldCharType": "begin"})
        code_run = ET.SubElement(start, W + "r")
        ET.SubElement(code_run, W + "instrText").text = ' TOC \\o "1-3" \\h '
        end_run = ET.SubElement(start, W + "r")
        ET.SubElement(end_run, W + "fldChar", {W + "fldCharType": "separate"})
        end = ET.Element(W + "p")
        ET.SubElement(ET.SubElement(end, W + "r"), W + "fldChar", {W + "fldCharType": "end"})
        result, _stats = self.convert(
            [
                paragraph("Contents", "Heading1"),
                start,
                paragraph("Cached field entry"),
                end,
                table(row(cell(""), cell(""))),
            ]
        )
        self.assertIn("Cached field entry", result)
        self.assertEqual(len(list(_reference.pandoc_html(result, self.pandoc).iter("tr"))), 1)

    def test_toc_page_reference_fields_are_dropped_from_their_links(self) -> None:
        for simple in (False, True):
            with self.subTest(simple=simple):
                entries: list[ET.Element] = []
                for name, title, page in (("_One", "First ", "1"), ("_Two", "Second ", "2")):
                    entry = hyperlink(title, anchor=name)
                    link = entry.find(W + "hyperlink")
                    assert link is not None
                    link.extend(page_reference(name, page, simple=simple))
                    entries.append(entry)
                result, report, _warnings = self.convert_reported(
                    [
                        paragraph("Contents.", "Heading1"),
                        *entries,
                        bookmark("_One", "First.", "Heading1"),
                        bookmark("_Two", "Second.", "Heading3"),
                    ],
                    output=self.root / f"toc-fields-{simple}.md",
                )

                parsed = _reference.pandoc_html(result, self.pandoc)
                self.assertEqual(
                    [(node.get("href"), "".join(node.itertext())) for node in parsed.iter("a")],
                    [("#first", "First"), ("#second", "Second")],
                )
                self.assertEqual(
                    report.records["fields"].applied["table-of-contents page number(s) dropped"],
                    2,
                )
                self.assertIn("## Second\n", result)

    def test_word_table_of_contents_keeps_entry_numbers_and_links_without_pages(self) -> None:
        result, report, _warnings = self.convert_reported(
            [
                paragraph("Contents"),
                toc_entry("_Toc1", "1", "Introduction", "3", begin=True),
                toc_entry("_Toc2", "2", "Setup", "5", end=True),
                bookmark("_Toc1", "Introduction", "Heading1"),
                paragraph("Text."),
                bookmark("_Toc2", "Setup", "Heading1"),
                paragraph("Steps."),
            ]
        )

        self.assertIn("[1 Introduction](#introduction)\n\n[2 Setup](#setup)\n", result)
        self.assertEqual(report.status("links"), "applied")
        self.assertEqual(report.status("checks"), "applied")
        self.assertFalse(report.records["checks"].skipped)

    def test_literal_word_placeholders_entities_and_tables_keep_their_text(self) -> None:
        split = paragraph("<sp", bold=True)
        split.append(_ooxml.word_run("eeds>"))
        result, _stats = self.convert(
            [
                paragraph("<Mode> &copy;.", "Heading3"),
                paragraph("Choose <speeds> &copy;."),
                paragraph("> Literal boundary"),
                split,
                hyperlink("<destination> &copy;", anchor="_Target"),
                bookmark("_Target", "Target"),
                table(
                    row(cell("Header"), header=True),
                    row(cell("<width> &copy;")),
                    columns=1,
                ),
                table(row(cell("<height> &copy;", colspan=2))),
            ]
        )

        parsed = _reference.pandoc_html(result, self.pandoc)
        visible = "".join(parsed.itertext())
        for literal in (
            "<Mode> &copy;",
            "Choose <speeds> &copy;.",
            "> Literal boundary",
            "<speeds>",
            "<destination> &copy;",
            "<width> &copy;",
            "<height> &copy;",
        ):
            self.assertIn(literal, visible)
        self.assertFalse(list(parsed.iter("speeds")))
        self.assertFalse(list(parsed.iter("blockquote")))
        self.assertEqual(_validation.collect_references(parsed).links, ["#mode-copy"])
        # The heading loses its period, not the semicolon of its entity-like text.
        self.assertEqual("".join(next(parsed.iter("h3")).itertext()), "<Mode> &copy;")

    def test_rich_table_heading_is_not_a_split_page_heading(self) -> None:
        self.convert(
            [
                paragraph("Chapter.", "Heading1"),
                table(row(cell(paragraph("In-cell heading.", "Heading1"))), columns=1),
            ],
            split_depth=2,
        )
        child = self.root / "Synthetic Guide" / "01-chapter.md"
        content = child.read_text(encoding="utf-8")
        self.assertIn("<h1>In-cell heading</h1>", content)
        self.assertEqual(
            _validation.collect_references(
                _reference.pandoc_html(content, self.pandoc)
            ).headings.count(1),
            1,
        )

    def test_urls_as_link_text_stay_one_link(self) -> None:
        # GFM's bare-URL autolinks would nest a link in the link text and break the outer one.
        url = "https://example.test/a_b_c?x=1&y=2"
        result, report, _warnings = self.convert_reported(
            [
                hyperlink(url, relation="site"),
                hyperlink("www.example.test/a_b", relation="www"),
                paragraph("See https://example.test/plain_path for details."),
            ],
            relations=(
                ("site", "hyperlink", url, "External"),
                ("www", "hyperlink", "https://www.example.test/a_b", "External"),
            ),
        )
        self.assertNotIn("[[", result)
        self.assertNotIn("&#92;", result)
        links = [
            (node.get("href"), "".join(node.itertext()))
            for node in _reference.pandoc_html(result, self.pandoc).iter("a")
        ]
        self.assertEqual(
            links,
            [
                (url, url),
                ("https://www.example.test/a_b", "www.example.test/a_b"),
                # A bare URL in prose becomes an autolink of its own.
                ("https://example.test/plain_path", "https://example.test/plain_path"),
            ],
        )
        self.assertIn("See <https://example.test/plain_path> for details.", result)
        self.assertEqual(report.status("checks"), "applied")
        self.assertFalse(report.records["checks"].skipped)

    def test_hyperlink_field_preserves_destination_and_cached_label(self) -> None:
        content = ET.Element(W + "p")
        field = ET.SubElement(
            content,
            W + "fldSimple",
            {
                W + "instr": ' HYPERLINK "https://example.test/item" \\o "Tooltip" ',
            },
        )
        field.append(_ooxml.word_run("Source label"))
        result, _stats = self.convert([content])
        self.assertIn("[Source label](https://example.test/item)", result)

    def test_complex_hyperlink_field_and_tooltip_preserve_link(self) -> None:
        content = ET.Element(W + "p")
        for kind in ("begin", "instruction", "separate", "text", "end"):
            run = ET.SubElement(content, W + "r")
            if kind == "instruction":
                ET.SubElement(
                    run, W + "instrText"
                ).text = ' HYPERLINK "https://example.test/a" \\o "Tip" '
            elif kind == "text":
                ET.SubElement(run, W + "t").text = "Cached label"
            else:
                ET.SubElement(run, W + "fldChar", {W + "fldCharType": kind})
        result, _stats = self.convert([content])
        self.assertIn("[Cached label](https://example.test/a)", result)

    def test_field_boundary_run_content_survives_real_conversion(self) -> None:
        for formatted in (False, True):
            with self.subTest(formatted=formatted):
                content = boundary_hyperlink_field()
                if formatted:
                    for run in (content[0], content[-1]):
                        properties = ET.Element(W + "rPr")
                        ET.SubElement(properties, W + "b")
                        run.insert(0, properties)
                    content[0].insert(2, ET.Element(W + "tab"))
                    for run in (content[0], content[-1]):
                        drawing = image().find(f"./{W}r/{W}drawing")
                        assert drawing is not None
                        run.insert(2, drawing)
                result, _stats = self.convert(
                    [content],
                    output=self.root / f"field-{formatted}.md",
                    relations=(("image", "image", "media/image.png", "Internal"),),
                    media={"image.png": PNG},
                )
                parsed = _reference.pandoc_html(result, self.pandoc)
                visible = "".join(parsed.itertext())
                self.assertIn("Before", visible)
                self.assertIn("Cached label", visible)
                self.assertIn("After", visible)
                self.assertEqual(
                    _validation.collect_references(parsed).links, ["https://example.test/plain"]
                )
                if formatted:
                    strong = ["".join(node.itertext()) for node in parsed.iter("strong")]
                    self.assertTrue(any("Before" in text for text in strong))
                    self.assertTrue(any("After" in text for text in strong))
                    self.assertEqual(len(_validation.collect_references(parsed).images), 2)

    def test_hyperlink_url_containing_toc_is_not_a_toc_field(self) -> None:
        target = "https://example.test/TOC"
        instruction = f' HYPERLINK "{target}" '
        for kind in ("complex", "simple"):
            with self.subTest(kind=kind):
                if kind == "complex":
                    content = boundary_hyperlink_field()
                    code = content.find(f".//{W}instrText")
                    assert code is not None
                    code.text = instruction
                else:
                    content = ET.Element(W + "p")
                    field = ET.SubElement(content, W + "fldSimple", {W + "instr": instruction})
                    field.append(_ooxml.word_run("Cached label"))
                result, _stats = self.convert([content], output=self.root / f"toc-url-{kind}.md")
                self.assertEqual(
                    _validation.collect_references(
                        _reference.pandoc_html(result, self.pandoc)
                    ).links,
                    [target],
                )

    def test_custom_style_outline_level_is_preserved(self) -> None:
        source = paragraph("Custom heading.")
        properties = ET.Element(W + "pPr")
        ET.SubElement(properties, W + "outlineLvl", {W + "val": "4"})
        source.insert(0, properties)
        result, _stats = self.convert([source])
        self.assertIn("##### Custom heading\n", result)

    def test_encrypted_container_is_rejected_without_bypass(self) -> None:
        self.source.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"encrypted office container")
        with (
            patch.object(_reference, "run_command") as run,
            self.assertRaisesRegex(_models.ConversionError, "not bypassed"),
        ):
            _pipeline.convert_docx(self.source)
        run.assert_not_called()

    def test_word_editing_protection_and_classification_metadata_remain_in_bundle(self) -> None:
        protection = (
            b'<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b'<w:documentProtection w:edit="readOnly" w:enforcement="1"/></w:settings>'
        )
        write_docx(
            self.source, [paragraph("Protected source")], extras={"word/settings.xml": protection}
        )
        diagnostics = self.root / "diagnostics"
        _pipeline.convert_docx(self.source, keep_intermediates=diagnostics)
        with zipfile.ZipFile(diagnostics / "reference.docx") as archive:
            self.assertEqual(archive.read("word/settings.xml"), protection)

    def test_one_level_lists_in_table_cells_become_lines(self) -> None:
        result, report, warnings = self.convert_reported(
            [
                table(
                    row(cell("Name"), cell("Steps"), header=True),
                    row(cell("A"), cell(numbered("first"), numbered("second"))),
                    row(cell("B"), cell(numbered("bullet", number="2"))),
                    row(cell("C"), cell("1. typed", numbered("listed"))),
                    columns=2,
                )
            ],
            extras={"word/numbering.xml": numbering_xml("decimal", 1, second="bullet")},
        )
        self.assertIn("| A | 1. first<br>2. second |", result)
        self.assertIn("| B | \u2022 bullet |", result)
        self.assertIn("| C | 1. typed<br>3. listed |", result)
        self.assertNotIn("<table", result)
        self.assertNotIn("remains HTML", warnings)
        # The final-table check expects the markers before Word's list items only.
        self.assertEqual(report.status("checks"), "applied")

    def test_indented_and_unnumbered_cell_paragraphs_become_lines(self) -> None:
        indented = paragraph("Indented note")
        properties = ET.Element(W + "pPr")
        ET.SubElement(properties, W + "ind", {W + "left": "720"})
        indented.insert(0, properties)
        # Word turns off one paragraph's number with numId 0; Pandoc makes it a further
        # paragraph of the item above.
        unnumbered = numbered("more about first")
        numbering = unnumbered.find(f"./{W}pPr/{W}numPr/{W}numId")
        assert numbering is not None
        numbering.set(W + "val", "0")
        result, report, warnings = self.convert_reported(
            [
                table(
                    row(cell("Name"), cell("Notes"), header=True),
                    row(cell("A"), cell(paragraph("Lead"), indented)),
                    row(cell("B"), cell(numbered("first"), unnumbered, numbered("second"))),
                    columns=2,
                )
            ],
            extras={"word/numbering.xml": numbering_xml("decimal", 1)},
        )
        self.assertIn("| A | Lead<br>Indented note |", result)
        self.assertIn("| B | 1. first<br>more about first<br>2. second |", result)
        self.assertNotIn("<table", result)
        self.assertNotIn("remains HTML", warnings)
        self.assertEqual(report.status("checks"), "applied")

    def test_sub_sup_mark_and_u_keep_tables_and_links_markdown(self) -> None:
        def run(text: str, name: str = "", value: str = "") -> ET.Element:
            node = _ooxml.word_run(text)
            if name:
                properties = ET.Element(W + "rPr")
                ET.SubElement(properties, W + name, {W + "val": value})
                node.insert(0, properties)
            return node

        def runs(*nodes: ET.Element) -> ET.Element:
            node = ET.Element(W + "p")
            node.extend(nodes)
            return node

        link = ET.Element(W + "p")
        ET.SubElement(link, W + "hyperlink", {R + "id": "site"}).append(
            run("Spec.pptx", "u", "single")
        )
        result, report, warnings = self.convert_reported(
            [
                table(
                    row(cell("Value"), cell("Notes"), header=True),
                    row(
                        cell(runs(run("x"), run("2", "vertAlign", "superscript"))),
                        cell(
                            runs(
                                run("H"),
                                run("2", "vertAlign", "subscript"),
                                run("O, "),
                                run("new", "highlight", "yellow"),
                                run(" and "),
                                run("a|b *c*", "u", "single"),
                            )
                        ),
                    ),
                    columns=2,
                ),
                link,
            ],
            relations=(("site", "hyperlink", "https://example.test/Spec.pptx", "External"),),
        )
        self.assertIn(
            r"| x<sup>2</sup> | H<sub>2</sub>O, <mark>new</mark> and <u>a\|b \*c\*</u> |", result
        )
        self.assertIn("[<u>Spec.pptx</u>](https://example.test/Spec.pptx)", result)
        self.assertNotIn("<table", result)
        self.assertNotIn("<a href", result)
        self.assertNotIn("remains HTML", warnings)
        self.assertEqual(report.status("checks"), "applied")

    def test_brackets_stay_escaped_only_where_they_could_be_read_differently(self) -> None:
        result, report, _warnings = self.convert_reported(
            [
                paragraph("Bits [3:0] of ctrl[7]; the text [a](b) is not a link."),
                numbered("[1:0]: low bits"),
                table(
                    row(cell("Field"), cell("Bits"), header=True),
                    row(cell("mode[1:0]"), cell("7) seven")),
                    columns=2,
                ),
            ],
            extras={"word/numbering.xml": numbering_xml("decimal", 1)},
        )
        self.assertIn(r"Bits [3:0] of ctrl[7]; the text \[a\](b) is not a link.", result)
        self.assertIn(r"1. \[1:0\]: low bits", result)
        self.assertIn("| mode[1:0] | 7) seven |", result)
        self.assertEqual(report.records["cleanup"].applied["unneeded escape(s) removed"], 7)

    def test_list_items_without_text_are_omitted_keeping_word_numbers(self) -> None:
        result, report, _warnings = self.convert_reported(
            [
                numbered(""),
                numbered("a"),
                numbered(""),
                numbered("b"),
                numbered(""),
                paragraph("Gap"),
                numbered("x", number="2"),
                numbered("", number="2"),
                numbered("y", number="2"),
            ],
            extras={"word/numbering.xml": numbering_xml("decimal", 1, second="bullet")},
        )
        # Word numbers the empty first item 1, so the list starts at 2; the empty item
        # between two others keeps its place so that "b" stays 4.
        self.assertRegex(result, r"(?m)^2\. a$")
        self.assertRegex(result, r"(?m)^3\.$")
        self.assertRegex(result, r"(?m)^4\. b$")
        self.assertNotRegex(result, r"(?m)^(1|5)\.")
        self.assertRegex(result, r"(?m)^- x$")
        self.assertRegex(result, r"(?m)^- y$")
        self.assertNotRegex(result, r"(?m)^-$")
        self.assertEqual(report.records["lists"].applied["empty Word list item(s) omitted"], 3)

    def test_deleted_list_items_and_tables_are_not_written(self) -> None:
        # Track Changes deletions: a list item's text and paragraph mark, and a table's rows.
        item = numbered("")
        run, properties = item.find(W + "r"), item.find(W + "pPr")
        assert run is not None and properties is not None
        item.remove(run)
        ET.SubElement(ET.SubElement(properties, W + "rPr"), W + "del", {W + "id": "1"})
        deleted = ET.SubElement(ET.SubElement(item, W + "del", {W + "id": "2"}), W + "r")
        ET.SubElement(deleted, W + "delText").text = "Removed item"
        rows = []
        for text in ("Removed cell", "Removed too"):
            deleted = row(cell(text))
            properties = ET.Element(W + "trPr")
            ET.SubElement(properties, W + "del", {W + "id": "3"})
            deleted.insert(0, properties)
            rows.append(deleted)
        result, _stats = self.convert(
            [
                paragraph("Intro"),
                table(*rows, columns=1),
                numbered("one"),
                item,
                numbered("two"),
            ],
            extras={"word/numbering.xml": numbering_xml("decimal", 1)},
        )
        self.assertNotIn("<table", result)
        self.assertNotIn("Removed", result)
        self.assertIn("1. one\n\n2. two", result)

    def test_accepted_view_and_package_metadata_are_consistent(self) -> None:
        content = ET.Element(W + "p")
        insertion = ET.SubElement(content, W + "ins")
        insertion.append(_ooxml.word_run("Accepted text"))
        deletion = ET.SubElement(content, W + "del")
        deletion.append(_ooxml.word_run("Deleted text"))
        custom = b"<Properties><Category>Example</Category></Properties>"
        write_docx(self.source, [content], extras={"docProps/custom.xml": custom})
        original = self.source.read_bytes()
        diagnostics = self.root / "debug"
        _pipeline.convert_docx(self.source, keep_intermediates=diagnostics)
        result = self.source.with_suffix(".md").read_text(encoding="utf-8")
        self.assertIn("Accepted text", result)
        self.assertNotIn("Deleted text", result)
        self.assertEqual(self.source.read_bytes(), original)
        with zipfile.ZipFile(diagnostics / "reference.docx") as archive:
            self.assertEqual(archive.read("docProps/custom.xml"), custom)

    def test_source_local_hyperlink_is_rebased_and_validated(self) -> None:
        asset = self.root / "Other File.txt"
        asset.write_text("retained local target", encoding="utf-8")
        output = self.root / "different" / "guide.md"
        result, _stats = self.convert(
            [hyperlink("Local target", relation="local")],
            output=output,
            relations=(("local", "hyperlink", "Other%20File.txt", "External"),),
        )
        self.assertIn("../Other%20File.txt", result)

    def test_replaced_page_tree_cannot_supply_stale_link_targets(self) -> None:
        output = self.root / "guide.md"
        output.write_text("# Old root\n", encoding="utf-8")
        old_pages = self.root / "guide"
        old_pages.mkdir()
        old_page = old_pages / "old.md"
        old_page.write_text("# Old page\n", encoding="utf-8")
        images = self.root / ".images" / "guide"
        images.mkdir(parents=True)
        (images / "old.svg").write_bytes(SVG)
        write_docx(
            self.source,
            [paragraph("New", "Heading1"), hyperlink("Stale", relation="local")],
            relations=(("local", "hyperlink", "guide/old.md", "External"),),
        )

        with (
            patch.object(
                _publication, "publish_outputs", wraps=_publication.publish_outputs
            ) as publish,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            report = _diagnostics.TransformReport()
            _pipeline.convert_docx(
                self.source, output, split_depth=1, replace_generated=True, report=report
            )

        publish.assert_called_once()
        self.assertIn(
            "a link into the page tree has no prepared page", report.records["checks"].skipped
        )
        self.assertIn("Link into the replaced page tree has no prepared page", stderr.getvalue())
        self.assertFalse(old_page.exists())
        self.assertTrue((old_pages / "01-new.md").is_file())

    def test_prepared_page_replaces_stale_content_for_fragment_validation(self) -> None:
        pages = self.root / "guide"
        pages.mkdir()
        child = pages / "01-new.md"
        child.write_text("# Old content\n", encoding="utf-8")
        outside = self.root / "outside.md"
        outside.write_text("# Outside\n", encoding="utf-8")
        write_docx(
            self.source,
            [
                bookmark("_Fresh", "New", "Heading1"),
                hyperlink("Fresh", relation="fresh"),
                hyperlink("Outside", relation="outside"),
            ],
            relations=(
                ("fresh", "hyperlink", "guide/01-new.md#new", "External"),
                ("outside", "hyperlink", "outside.md#outside", "External"),
            ),
        )

        _pipeline.convert_docx(
            self.source, self.root / "guide.md", split_depth=1, replace_generated=True
        )

        self.assertIn("# New", child.read_text(encoding="utf-8"))
        self.assertNotIn("_Fresh", child.read_text(encoding="utf-8"))
        self.assertEqual(outside.read_text(encoding="utf-8"), "# Outside\n")

    def test_depth_zero_allows_links_to_retained_old_pages(self) -> None:
        old_pages = self.root / "guide"
        old_pages.mkdir()
        old_page = old_pages / "old.md"
        old_page.write_text("# Old\n", encoding="utf-8")
        write_docx(
            self.source,
            [hyperlink("Retained", relation="old")],
            relations=(("old", "hyperlink", "guide/old.md#old", "External"),),
        )

        _pipeline.convert_docx(self.source, self.root / "guide.md")

        self.assertEqual(old_page.read_text(encoding="utf-8"), "# Old\n")
        self.assertIn("guide/old.md#old", (self.root / "guide.md").read_text(encoding="utf-8"))

    def test_replaced_image_tree_cannot_supply_stale_link_targets(self) -> None:
        output = self.root / "guide.md"
        output.write_text("# Old root\n", encoding="utf-8")
        images = self.root / ".images" / "guide"
        images.mkdir(parents=True)
        old_image = images / "old.svg"
        old_image.write_bytes(SVG)
        write_docx(
            self.source,
            [hyperlink("Stale asset", relation="old")],
            relations=(("old", "hyperlink", ".images/guide/old.svg", "External"),),
        )

        with (
            patch.object(
                _publication, "publish_outputs", wraps=_publication.publish_outputs
            ) as publish,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            report = _diagnostics.TransformReport()
            _pipeline.convert_docx(self.source, output, replace_generated=True, report=report)

        publish.assert_called_once()
        self.assertIn("a linked image file is missing", report.records["checks"].skipped)
        self.assertIn("Missing linked asset in guide.md: .images/guide/old.svg", stderr.getvalue())
        self.assertFalse(old_image.exists())

    def test_external_url_escaping_survives_real_conversion_and_tables(self) -> None:
        for index, (target, expected) in enumerate(
            (
                ("https://example.test/topic_(detail)", "https://example.test/topic_%28detail%29"),
                ("https://example.test/R\u00e9sum\u00e9", "https://example.test/R%C3%A9sum%C3%A9"),
                (
                    "https://example.test/a%2Fb?q=x%26y&percent=%2525",
                    "https://example.test/a%2Fb?q=x%26y&percent=%2525",
                ),
                (
                    "https://example.test/?q=one&copy;=two&notit=three",
                    "https://example.test/?q=one&copy;=two&notit=three",
                ),
                (
                    "https://example.test/?literal=&amp;value",
                    "https://example.test/?literal=&amp;value",
                ),
                (
                    "https://[2001:db8::1]/topic_(detail)",
                    "https://[2001:db8::1]/topic_%28detail%29",
                ),
            )
        ):
            with self.subTest(target=target):
                result, _stats = self.convert(
                    [
                        hyperlink("Outside", relation="url"),
                        table(
                            row(cell("Link"), header=True),
                            row(cell(hyperlink("Pipe", relation="url"))),
                            columns=1,
                        ),
                        table(row(cell(hyperlink("HTML", relation="url"), colspan=2))),
                    ],
                    output=self.root / f"url-{index}.md",
                    relations=(("url", "hyperlink", target, "External"),),
                )
                rendered = _validation.collect_references(
                    _reference.pandoc_html(result, self.pandoc)
                ).links
                self.assertEqual(rendered[:2], [expected, expected])
                self.assertEqual(rendered[2], target)

    def test_pandoc_generated_docx_preserves_unicode_parenthesized_external_url(self) -> None:
        target = "https://example.test/R\u00e9sum\u00e9_(detail)"
        _reference.run_command(
            [self.pandoc, "--from=gfm+raw_html", "--to=docx", f"--output={self.source}"],
            "Synthetic Markdown-to-Word",
            input_text=f"# Unicode guide\n\n[External reference]({target})\n",
        )
        output = self.root / "generated.md"

        result = subprocess.run(
            [*COMMAND, str(self.source), "-o", str(output)],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        links = _validation.collect_references(
            _reference.pandoc_html(output.read_text(encoding="utf-8"), self.pandoc)
        ).links
        self.assertEqual(links, ["https://example.test/R%C3%A9sum%C3%A9_%28detail%29"])

    def test_broken_local_hyperlink_is_kept_and_reported(self) -> None:
        result, report, warnings = self.convert_reported(
            [hyperlink("Missing", relation="local")],
            relations=(("local", "hyperlink", "missing.txt", "External"),),
        )
        self.assertIn("[Missing](missing.txt)", result)
        self.assertIn(
            "checks: Broken retained local link in Synthetic Guide.md: missing.txt", warnings
        )
        summary = report.summary()
        self.assertIn("  checks: 1 issue(s)", summary)
        self.assertIn("    issue (1): a local link target does not exist", summary)

    def test_malformed_uri_is_kept_and_reported(self) -> None:
        write_docx(
            self.source,
            [hyperlink("Broken", relation="broken")],
            relations=(("broken", "hyperlink", "https://[bad", "External"),),
        )
        diagnostics = self.root / "diagnostics"
        report = _diagnostics.TransformReport()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            _pipeline.convert_docx(self.source, keep_intermediates=diagnostics, report=report)
        self.assertIn("Broken", self.source.with_suffix(".md").read_text(encoding="utf-8"))
        self.assertIn("Invalid retained URI", stderr.getvalue())
        saved = json.loads((diagnostics / "diagnostics.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "published")
