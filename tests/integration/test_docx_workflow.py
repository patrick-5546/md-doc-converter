"""docx-to-md end to end: synthetic Word documents, converted with Pandoc."""

from __future__ import annotations

import copy
import inspect
import os
import re
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from unittest import mock
from xml.etree import ElementTree as ET

import pytest

from md_doc_converter import executables
from md_doc_converter.converters import packages
from md_doc_converter.converters.docx import diagnostics as _diagnostics
from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import normalization as _normalization
from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx import pipeline as _pipeline
from md_doc_converter.converters.docx import prepare as _prepare
from md_doc_converter.converters.docx import publication as _publication
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import tables as _tables
from md_doc_converter.converters.docx import tree as _tree
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
    font_run,
    formatted,
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
# Word document relationships: (identifier, type, target, target mode).
Relations = tuple[tuple[str, str, str, str], ...]
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


# The markdownlint rules, as rumdl checks them, that converted Markdown must follow.
CONVERTED_MARKDOWN_RULES = Path(__file__).parents[1] / "support" / "converted.rumdl.toml"


def linted[**Options](
    convert: Callable[Options, dict[str, int]],
) -> Callable[Options, dict[str, int]]:
    """*convert*, then a check of the Markdown it published against markdownlint's rules."""
    signature = inspect.signature(convert)
    rumdl = Path(sysconfig.get_path("scripts")) / "rumdl"

    def checked(*args: Options.args, **kwargs: Options.kwargs) -> dict[str, int]:
        stats = convert(*args, **kwargs)
        call = signature.bind(*args, **kwargs)
        call.apply_defaults()
        options = call.arguments
        if not options["dry_run"]:
            published = _publication.requested_output(options["input_path"], options["output_path"])
            result = subprocess.run(
                [
                    rumdl,
                    "check",
                    "--config",
                    CONVERTED_MARKDOWN_RULES,
                    "--no-cache",
                    "--output-format",
                    "concise",
                    published,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, result.stdout + result.stderr
        return stats

    return checked


@pytest.fixture(autouse=True)
def lint_converted_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check the Markdown that each conversion in these tests publishes, including combinations
    that no expected output covers."""
    monkeypatch.setattr(_pipeline, "convert_docx", linted(_pipeline.convert_docx))


@dataclass(frozen=True)
class Converter:
    """Writes a Word document of synthetic body elements to the source path and converts it.

    `result, stats = convert(body)` returns the Markdown and the conversion's statistics;
    `result, report, warnings = convert.reported(body)` also keeps a transform report.
    """

    source: Path
    capsys: pytest.CaptureFixture[str]

    def __call__(
        self,
        body: list[ET.Element],
        *,
        output: Path | None = None,
        qualify: bool = False,
        relations: Relations = (),
        media: dict[str, bytes] | None = None,
        report: _diagnostics.TransformReport | None = None,
        extras: dict[str, bytes] | None = None,
        styles: tuple[ET.Element, ...] = (),
    ) -> tuple[str, dict[str, int]]:
        """Convert a document of *body*; return the Markdown for *output* and the statistics."""
        write_docx(
            self.source, body, relations=relations, media=media, extras=extras, styles=styles
        )
        stats = _pipeline.convert_docx(
            self.source,
            output,
            qualify_duplicate_headings=qualify,
            report=report,
        )
        return _publication.requested_output(self.source, output).read_text(encoding="utf-8"), stats

    def reported(
        self,
        body: list[ET.Element],
        *,
        output: Path | None = None,
        relations: Relations = (),
        media: dict[str, bytes] | None = None,
        extras: dict[str, bytes] | None = None,
        styles: tuple[ET.Element, ...] = (),
    ) -> tuple[str, _diagnostics.TransformReport, str]:
        """Convert with a transform report; return the output, the report, and its warnings."""
        report = _diagnostics.TransformReport()
        self.capsys.readouterr()  # Earlier output is not this conversion's warnings.
        result, _stats = self(
            body,
            output=output,
            relations=relations,
            media=media,
            report=report,
            extras=extras,
            styles=styles,
        )
        return result, report, self.capsys.readouterr().err


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """Where a test writes its Word document; the Markdown goes next to it by default."""
    return tmp_path / "Synthetic Guide.docx"


@pytest.fixture
def convert(source: Path, capsys: pytest.CaptureFixture[str]) -> Converter:
    """Converts synthetic Word documents written to `source`; see `Converter`."""
    return Converter(source, capsys)


def list_shape(root: ET.Element) -> list[tuple[str, str, int, int]]:
    """Each list in *root*: its tag, numbering type, start number, and number of items."""
    return [
        (str(node.tag), node.get("type", "1"), int(node.get("start", "1")), len(node.findall("li")))
        for node in root.iter()
        if node.tag in {"ol", "ul"}
    ]


def hide_pandoc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let md-doc-converter find every program but Pandoc, as where Pandoc is not installed."""
    original = executables.which

    def which(name: str, path: str | None = None) -> str | None:
        return None if name == "pandoc" else original(name, path)

    monkeypatch.setattr(executables, "which", which)


def test_default_keeps_step_and_status_labels_and_index_content(convert: Converter) -> None:
    result, stats = convert(
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
    # Headings keep their punctuation, as bold labels do, and their levels.
    assert "\n# Title.\n" in f"\n{result}"
    assert "\n#### Gap.\n" in result
    assert "**1)**\n\nDo the thing." in result
    assert "**Closed.**" in result
    assert "# Contents" in result
    assert "Original index entry" in result
    assert result.count("\n### Flow.\n") == 2
    assert stats["qualified_headings"] == 0
    assert result.endswith("\n")
    assert not result.endswith("\n\n")


def test_ordered_list_start_and_nesting_survive_conversion(source: Path, pandoc: str) -> None:
    write_docx(
        source,
        [
            paragraph("Numbers.", "Heading1"),
            numbered("Seven"),
            numbered("Nested seven", 1),
            numbered("Nested eight", 1),
            numbered("Eight"),
        ],
        extras={"word/numbering.xml": numbering_xml()},
    )
    _pipeline.convert_docx(source)
    content = source.with_suffix(".md").read_text(encoding="utf-8")
    shape = list_shape(_reference.pandoc_html(content, pandoc))
    assert shape == [("ol", "1", 7, 2), ("ol", "1", 7, 2)]


def test_unordered_word_lists_are_dash_markdown(source: Path, pandoc: str) -> None:
    write_docx(
        source,
        [numbered("First"), numbered("Nested", 1), numbered("Last")],
        extras={"word/numbering.xml": numbering_xml("bullet", 1)},
    )
    _pipeline.convert_docx(source)
    content = source.with_suffix(".md").read_text(encoding="utf-8")
    assert "- " in content
    assert "* " not in content
    assert "+ " not in content
    assert list_shape(_reference.pandoc_html(content, pandoc)) == [
        ("ul", "1", 1, 2),
        ("ul", "1", 1, 1),
    ]


def test_nondecimal_ordered_list_becomes_a_numbered_markdown_list(
    source: Path, pandoc: str
) -> None:
    # Markdown numbers lists only with decimals; Word's letters become numbers, not HTML.
    write_docx(
        source,
        [numbered("First"), numbered("Second")],
        extras={"word/numbering.xml": numbering_xml("lowerLetter", 3)},
    )
    _pipeline.convert_docx(source)
    content = source.with_suffix(".md").read_text(encoding="utf-8")
    assert "<" not in content
    assert re.search(r"^3\.\s+First\n+4\.\s+Second\n", content)
    assert list_shape(_reference.pandoc_html(content, pandoc)) == [("ol", "1", 3, 2)]


def test_list_defined_by_a_word_list_style_is_a_list(convert: Converter, pandoc: str) -> None:
    # Pandoc ignores w:numStyleLink; the converter resolves it for both converters.
    result, report, warnings = convert.reported(
        [
            paragraph("Intro"),
            numbered("Alpha", number="2"),
            numbered("Beta", number="2"),
            paragraph("Outro"),
        ],
        styles=(word_style("Bullets", "numbering", number="1"),),
        extras={"word/numbering.xml": numbering_xml("bullet", 1, style_link="Bullets")},
    )
    assert report.status("lists") == "applied"
    assert re.search(r"Intro\n\n- Alpha\n+- Beta\n\nOutro", result)
    assert list_shape(_reference.pandoc_html(result, pandoc)) == [("ul", "1", 1, 2)]
    assert "lists:" not in warnings


def test_paragraphs_numbered_through_their_style_are_list_items(
    convert: Converter, pandoc: str
) -> None:
    # The numbering comes from the style that the paragraphs' style is based on.
    result, report, warnings = convert.reported(
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
    assert report.status("lists") == "applied"
    assert re.search(r"Intro\n\n- Alpha\n+- Beta\n\nOutro", result)
    assert list_shape(_reference.pandoc_html(result, pandoc)) == [("ul", "1", 1, 2)]
    assert "lists:" not in warnings


@pytest.mark.parametrize(
    "extras",
    [
        pytest.param({"word/numbering.xml": numbering_xml("bullet", 1)}, id="undefined-list"),
        pytest.param(None, id="no-numbering-part"),
    ],
)
def test_paragraphs_without_a_defined_word_list_are_not_list_items(
    extras: dict[str, bytes] | None, convert: Converter
) -> None:
    body = [paragraph("Intro"), numbered("Alpha", number="9"), numbered("Beta", number="9")]
    result, report, warnings = convert.reported(body, extras=extras)
    assert report.status("lists") == "not needed"
    assert "Intro\n\nAlpha\n\nBeta" in result
    assert "lists:" not in warnings


def test_lists_after_fields_pandoc_cannot_close_keep_word_numbering(convert: Converter) -> None:
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
    result, report, _warnings = convert.reported(
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
    assert report.status("lists") == "applied"
    assert re.search(r"Intro\n\n7\.\s+Alpha\n+8\.\s+Beta\n\nOutro", result)
    for text in ("[Scope](#scope)", "Revision 2", "Figure 1"):
        assert f"\n{text}\n" in f"\n{result}"
    assert (
        "  fields: applied (2 field(s) unwrapped, 1 table-of-contents page number(s) dropped)"
        in report.summary()
    )


def test_lists_that_start_at_a_nested_level_keep_word_numbering(convert: Converter) -> None:
    # A list that starts at a nested level is a list of its own, as Word numbers it.
    result, report, warnings = convert.reported(
        [paragraph("Intro"), numbered("Alpha", 1), numbered("Beta", 1), paragraph("Outro")],
        extras={"word/numbering.xml": numbering_xml()},
    )
    assert report.status("lists") == "applied"
    assert re.search(r"Intro\n\n7\.\s+Alpha\n+8\.\s+Beta\n\nOutro", result)
    assert "lists:" not in warnings


def test_consecutive_lists_at_a_nested_level_stay_separate_lists(
    convert: Converter, pandoc: str
) -> None:
    # Lists at a nested level, one after another, stay separate lists.
    result, report, warnings = convert.reported(
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
    assert report.status("lists") == "applied"
    assert re.search(r"Intro\n\n- A one\n+- A two\n\n7\.\s+B one\n+8\.\s+B two\n\nOutro", result)
    parsed = _reference.pandoc_html(result, pandoc)
    assert [node.tag for node in parsed] == ["p", "ul", "ol", "p"]
    assert "lists:" not in warnings


def test_numbered_outline_level_heading_stays_a_list_item(convert: Converter) -> None:
    # Pandoc reads it as a list item; only a "heading N" style is a heading.
    outlined = numbered("Gamma")
    properties = outlined.find(W + "pPr")
    assert properties is not None
    ET.SubElement(properties, W + "outlineLvl", {W + "val": "1"})
    result, report, warnings = convert.reported(
        [paragraph("Intro"), numbered("Alpha"), numbered("Beta"), outlined, paragraph("Outro")],
        extras={"word/numbering.xml": numbering_xml()},
    )
    assert report.status("lists") == "applied"
    assert re.search(r"Intro\n\n7\.\s+Alpha\n+8\.\s+Beta\n+9\.\s+Gamma\n\nOutro", result)
    assert (
        "    not applied (1): Word numbers this outline-level heading as a list item; "
        "kept as a list item" in report.summary()
    )
    assert "lists:" not in warnings


def test_headings_keep_their_trailing_punctuation(convert: Converter) -> None:
    outlined = numbered("Gamma.")
    properties = outlined.find(W + "pPr")
    assert properties is not None
    ET.SubElement(properties, W + "outlineLvl", {W + "val": "1"})
    result, report, _warnings = convert.reported(
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
    assert "# Overview:\n" in result
    assert "## Why?\n" in result
    assert "## Steps.\n" in result
    assert re.search(r"7\.\s+Alpha\.\n+8\.\s+Gamma\.", result)
    # A heading's ID has no punctuation.
    assert "[Back to the steps](#steps)" in result
    assert "  headings: partial (3 applied, 1 not applied)" in report.summary()
    assert report.status("checks") == "applied"


def test_skipped_heading_levels_are_kept_and_reported(convert: Converter) -> None:
    result, report, _warnings = convert.reported(
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
    assert levels == [
        ("##", "Start"),
        ("#", "One"),
        ("###", "Three"),
        ("####", "Four"),
        ("###", "Three again"),
        ("##", "Two"),
        ("#####", "Five"),
    ]
    assert "<h4>In a cell</h4>" in result
    # The first heading, and headings in tables, skip nothing.
    assert [finding for finding in report.findings if finding.startswith("headings:")] == [
        "headings: Word heading 'Three', at level 3, follows a level 1 heading; kept at its level",
        "headings: Word heading 'Five', at level 5, follows a level 2 heading; kept at its level",
    ]
    assert report.kinds[_prepare.SKIPPED_LEVEL] == 2
    assert report.sections[_prepare.SKIPPED_LEVEL] == "word"
    assert report.status("checks") == "applied"


def test_bare_web_and_e_mail_addresses_become_autolinks(convert: Converter) -> None:
    result, report, _warnings = convert.reported(
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
    assert "Owner: Jane <jane@example.com>; see <https://example.com/spec>." in result
    assert "<https://linked.example>" in result
    assert "| <https://cell.example/p> |" in result
    assert "| Reviewer <reviewer@cell.example> |" in result
    assert "  links: applied (1, 4 bare web or e-mail address(es) linked)" in report.summary()
    assert report.status("checks") == "applied"


def test_numbering_off_paragraphs_stay_with_the_list_item_before_them(convert: Converter) -> None:
    result, report, warnings = convert.reported(
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
    assert report.status("lists") == "applied"
    assert re.search(
        r"7\.\s+Alpha\n+ {3}More alpha\n+8\.\s+Beta\n+ {3}More beta\n\nOutro\n\nLoose", result
    )
    assert "lists:" not in warnings


def test_paragraphs_of_lists_that_show_no_number_are_paragraphs(convert: Converter) -> None:
    # Word uses a list whose levels show no number or bullet to indent paragraphs.
    numbering = ET.fromstring(numbering_xml())
    abstract = ET.Element(W + "abstractNum", {W + "abstractNumId": "2"})
    for index in range(3):
        level = ET.SubElement(abstract, W + "lvl", {W + "ilvl": str(index)})
        ET.SubElement(level, W + "numFmt", {W + "val": "decimal"})
        ET.SubElement(level, W + "lvlText", {W + "val": ""})
    numbering.insert(1, abstract)
    instance = ET.SubElement(numbering, W + "num", {W + "numId": "2"})
    ET.SubElement(instance, W + "abstractNumId", {W + "val": "2"})
    result, report, warnings = convert.reported(
        [
            paragraph("Intro"),
            numbered("Alpha"),
            numbered("Indented under alpha", number="2"),
            paragraph("Middle"),
            numbered("Indented alone", number="2"),
            paragraph("Noted", "Note"),
            table(row(cell(numbered("In a cell", number="2"))), columns=1),
        ],
        extras={"word/numbering.xml": _ooxml.word_xml_bytes(numbering)},
        styles=(word_style("Note", number="2"),),
    )
    assert re.search(
        r"Intro\n\n7\.\s+Alpha\n+ {3}Indented under alpha\n\nMiddle\n\n"
        r"Indented alone\n\nNoted\n",
        result,
    )
    assert "| In a cell |" in result
    assert "4 Word list paragraph(s) without a number or bullet kept as paragraphs" in "\n".join(
        report.summary()
    )
    assert "lists:" not in warnings


def test_empty_paragraphs_inside_a_word_list_do_not_split_it(
    convert: Converter, pandoc: str
) -> None:
    # Word numbers a list on past an empty paragraph; Pandoc would end the list there.
    result, report, warnings = convert.reported(
        [
            paragraph("Intro"),
            numbered("Alpha"),
            numbered("Alpha one", 1),
            paragraph(""),
            paragraph(" "),
            numbered("Alpha one a", 2),
            numbered("Beta"),
            # Another list, and a paragraph after the list, are not joined to it.
            paragraph(""),
            numbered("Bullet", number="2"),
            paragraph(""),
            paragraph("Outro"),
        ],
        extras={"word/numbering.xml": numbering_xml(second="bullet")},
    )
    assert list_shape(_reference.pandoc_html(result, pandoc)) == [
        ("ol", "1", 7, 2),
        ("ol", "1", 7, 1),
        ("ol", "1", 7, 1),
        ("ul", "1", 1, 1),
    ]
    assert re.search(r"7\.\s+Alpha one a\n+8\.\s+Beta\n", result)
    assert "2 empty paragraph(s) inside a Word list omitted" in "\n".join(report.summary())
    assert "lists:" not in warnings


def test_paragraphs_pandoc_reads_as_headings_are_not_list_items(convert: Converter) -> None:
    contents = word_style("TOCHeading", based_on="Heading1", number="1")
    properties = contents.find(W + "pPr")
    assert properties is not None
    ET.SubElement(properties, W + "outlineLvl", {W + "val": "9"})
    result, report, warnings = convert.reported(
        [
            paragraph("Contents", "TOCHeading"),
            paragraph("Intro"),
            numbered("Alpha"),
            paragraph("Outro"),
        ],
        styles=(contents,),
        extras={"word/numbering.xml": numbering_xml()},
    )
    assert report.status("lists") == "applied"
    assert "Contents" in result
    assert "lists:" not in warnings


def test_backticks_backslashes_and_hashes_keep_their_text(convert: Converter, pandoc: str) -> None:
    texts = (
        "Use `uvm_info and `uvm_error.",
        "# of entries",
        "Path C:\\tools\\a\\*b and \\\\server.",
    )
    items = ("Run `run_test and `uvm_fatal.", "Done")
    result, report, _warnings = convert.reported(
        [*(paragraph(text) for text in texts), *(numbered(item) for item in items)],
        extras={"word/numbering.xml": numbering_xml()},
    )
    parsed = _reference.pandoc_html(result, pandoc)

    def shown(tag: str) -> list[str]:
        return [" ".join("".join(node.itertext()).split()) for node in parsed.iter(tag)]

    assert shown("p")[: len(texts)] == list(texts)
    assert shown("li") == list(items)
    assert not list(parsed.iter("code"))
    assert not list(parsed.iter("h1"))
    assert report.status("lists") == "applied"
    assert report.status("checks") == "applied"


def test_brackets_stay_text_rather_than_links(convert: Converter, pandoc: str) -> None:
    result, report, _warnings = convert.reported(
        [
            paragraph("Set bit[3](W1C) and see [note]."),
            numbered("Clear field[7:0](RW) first."),
            numbered("Done"),
        ],
        extras={"word/numbering.xml": numbering_xml()},
    )
    parsed = _reference.pandoc_html(result, pandoc)
    assert _validation.collect_references(parsed).links == []
    visible = "".join(parsed.itertext())
    for literal in ("Set bit[3](W1C) and see [note].", "Clear field[7:0](RW) first."):
        assert literal in visible
    assert report.status("lists") == "applied"
    assert report.status("checks") == "applied"


def test_symbol_fonts_and_unclosable_emphasis_keep_lists(convert: Converter) -> None:
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
    result, report, warnings = convert.reported(
        [
            line(_ooxml.word_run("Passed "), check),
            line(_ooxml.word_run("Reviewed "), copy.deepcopy(check), item=True),
            line(styled("Note:", bold=True), _ooxml.word_run("next"), item=True),
            line(_ooxml.word_run("A "), styled("\u00ae", font="Symbol"), _ooxml.word_run(" B")),
        ],
        extras={"word/numbering.xml": numbering_xml()},
    )
    # Pandoc maps symbol fonts to Unicode; Markdown cannot close bold that ends in punctuation
    # right before a letter, so that bold is HTML.
    assert "Passed ✔\n" in result
    assert "Reviewed ✔" in result
    assert "A → B" in result
    assert "ü" not in result
    assert "<strong>Note:</strong>next" in result
    assert report.status("lists") == "applied"
    assert "lists:" not in warnings


def test_vml_text_boxes_in_list_items_keep_the_list(convert: Converter) -> None:
    # A text box's text follows its paragraph, here a list item, which stays in its list.
    item = numbered("Lead ")
    item.extend(list(text_box("Box label", legacy=True)))
    item.append(_ooxml.word_run(" tail"))
    result, report, _warnings = convert.reported(
        [paragraph("Intro"), item, numbered("Beta"), paragraph("Outro")],
        extras={"word/numbering.xml": numbering_xml()},
    )
    assert report.status("lists") == "applied"
    assert re.search(r"7\.\s+Lead tail\n\nBox label\n\n8\.\s+Beta\n", result)


def test_lists_inside_tables_keep_word_numbers(source: Path) -> None:
    write_docx(
        source,
        [
            table(
                row(cell("Header"), header=True),
                row(cell(numbered("Seven"), numbered("Eight"))),
                columns=1,
            ),
        ],
        extras={"word/numbering.xml": numbering_xml()},
    )
    _pipeline.convert_docx(source)
    content = source.with_suffix(".md").read_text(encoding="utf-8")
    assert "| 7. Seven<br>8. Eight |" in content


def test_multiple_empty_headerless_and_nested_tables(convert: Converter, pandoc: str) -> None:
    nested = table(row(cell("nested")), columns=1)
    result, stats = convert(
        [
            paragraph("Before"),
            table(row(cell(""), cell("")), row(cell("data"), cell("")), row(cell(""), cell(""))),
            table(),
            table(row(cell(""), cell(""))),
            table(row(cell("Outer", nested), cell("Tail"))),
            paragraph("After"),
        ]
    )
    parsed = _reference.pandoc_html(result, pandoc)
    tables = list(parsed.iter("table"))
    # The first table's empty rows go, so its data row leads a Markdown table. A table without
    # rows shows nothing and goes; the nested table is part of its outer table's HTML.
    assert [len(_tables.html_rows(item)) for item in tables] == [1, 1, 1, 1]
    assert stats["tables"] == 3
    assert len(list(parsed.iter("thead"))) == 1
    assert "nested" in result
    assert "Before" in result
    assert "After" in result


def test_rows_with_grid_gaps_keep_their_word_cells(convert: Converter) -> None:
    # Pandoc fills a row's grid gaps, and a short row, with an empty cell per grid column, so
    # each cell keeps its column and the table can be a pipe table.
    def gapped(before: int, after: int, *texts: str) -> ET.Element:
        result = row(*(cell(text) for text in texts))
        properties = ET.Element(W + "trPr")
        for name, value in (("gridBefore", before), ("gridAfter", after)):
            if value:
                ET.SubElement(properties, W + name, {W + "val": str(value)})
        result.insert(0, properties)
        return result

    result, report, warnings = convert.reported(
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
    assert report.status("tables") == "applied"
    assert "tables:" not in warnings
    assert result == "| a | b | c |\n| --- | --- | --- |\n| | | z |\n| x | | |\n| p | q | |\n"


def test_grid_columns_without_a_width_keep_their_cells(convert: Converter) -> None:
    # Pandoc leaves out such a column, and with it the cells of each row beyond the rest.
    word = table(row(cell("a"), cell("b")), row(cell("1"), cell("2")))
    grid = word.find(W + "tblGrid")
    assert grid is not None
    grid[0].attrib.clear()
    result, _stats = convert([word])
    assert result == "| a | b |\n| --- | --- |\n| 1 | 2 |\n"


def test_rows_wider_than_the_table_grid_keep_every_cell(convert: Converter, pandoc: str) -> None:
    # Pandoc drops the cells beyond w:tblGrid, which Word tolerates.
    result, report, warnings = convert.reported(
        [
            table(
                row(cell("a"), cell("b"), cell("c")),
                row(cell("x"), cell("y"), cell("z")),
                columns=2,
            )
        ]
    )
    assert report.status("tables") == "applied"
    assert "tables:" not in warnings
    parsed = next(_reference.pandoc_html(result, pandoc).iter("table"))
    assert [["".join(node.itertext()) for node in tr] for tr in _tables.html_rows(parsed)] == [
        ["a", "b", "c"],
        ["x", "y", "z"],
    ]


def test_a_narrow_first_body_row_keeps_every_table_cell(convert: Converter, pandoc: str) -> None:
    # Pandoc takes a table's columns from its first body row and drops the rest, and pads
    # short rows.
    title = row(cell("Title", colspan=2))
    result, report, warnings = convert.reported(
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
    assert report.status("tables") == "applied"
    assert "tables:" not in warnings
    shapes = [
        [
            [(node.get("colspan", "1"), "".join(node.itertext())) for node in tr]
            for tr in _tables.html_rows(rendered)
        ]
        for rendered in _reference.pandoc_html(result, pandoc).iter("table")
    ]
    assert shapes == [
        [
            [("1", "h1"), ("1", "h2"), ("1", "h3")],
            [("1", "a"), ("1", "b"), ("1", "")],
            [("1", "x"), ("1", "y"), ("1", "z")],
        ],
        [[("2", "Title"), ("1", "")], [("1", "p"), ("1", "q"), ("1", "r")]],
    ]


def test_a_blank_header_row_goes_and_the_next_row_leads(convert: Converter, pandoc: str) -> None:
    result, _stats = convert(
        [
            table(
                row(cell(""), cell(""), header=True),
                row(cell("A | B"), cell("first", "", "second")),
                row(cell(""), cell("")),
            ),
        ]
    )
    assert "<table" not in result
    # The row that leads is the table's only row, which keeps its lines.
    assert r"| A \| B | first<br><br>second |" in result
    parsed = _reference.pandoc_html(result, pandoc)
    actual = next(parsed.iter("table"))
    assert len(_tables.html_rows(actual)) == 1


def test_nonempty_header_uses_editable_multiline_pipe_table(
    convert: Converter, pandoc: str
) -> None:
    result, stats = convert(
        [
            table(
                row(cell("Name"), cell("Details"), header=True),
                row(cell("A | B"), cell("first", "", "second")),
                row(cell(""), cell("")),
            ),
        ]
    )
    assert "| Name | Details |" in result
    assert "| --- | --- |" in result
    assert r"A \| B" in result
    assert "first<br><br>second" in result
    assert stats["multiline_tables"] == 1
    assert len(list(_reference.pandoc_html(result, pandoc).iter("tr"))) == 2


def test_aligned_word_cells_have_plain_separators_without_changing_literal_values(
    convert: Converter, pandoc: str
) -> None:
    headers: list[ET.Element] = []
    for alignment in ("left", "center", "right"):
        heading = paragraph(alignment.title())
        props = ET.Element(W + "pPr")
        ET.SubElement(props, W + "jc", {W + "val": alignment})
        heading.insert(0, props)
        headers.append(cell(heading))
    result, _stats = convert(
        [
            table(
                row(*headers, header=True),
                row(cell(":---:"), cell("---:"), cell(":---")),
                columns=3,
            )
        ]
    )
    assert "| Left | Center | Right |\n| --- | --- | --- |\n" in result
    assert "| :---: | ---: | :--- |\n" in result
    assert "text-align" not in _reference.html_element(_reference.pandoc_html(result, pandoc))


def test_signal_identifiers_are_readable_in_prose_and_table_cells(convert: Converter) -> None:
    result, _stats = convert(
        [
            paragraph("Signal _ctl_s_ready is low."),
            table(row(cell("Signal")), row(cell("_ctl_s_ready")), columns=1),
        ]
    )
    assert "Signal _ctl_s_ready is low." in result
    assert "| _ctl_s_ready |" in result
    assert r"\_ctl_s_ready" not in result


def test_an_all_empty_table_keeps_its_first_row(convert: Converter, pandoc: str) -> None:
    result, _stats = convert(
        [
            paragraph("Tables.", "Heading1"),
            table(row(cell(""), cell("")), row(cell(""), cell("")), row(cell(""), cell(""))),
        ]
    )
    assert len(list(_reference.pandoc_html(result, pandoc).iter("tr"))) == 1


def test_rows_and_tables_without_cells_are_omitted(convert: Converter) -> None:
    result, _stats = convert(
        [
            table(row(), row(cell("A"), cell("B")), row()),
            paragraph("Between"),
            table(row(), row()),
        ]
    )
    # Rows without cells show nothing, so they go, and so does a table left without rows.
    assert result == "| A | B |\n| --- | --- |\n\nBetween\n"


def test_empty_rows_are_omitted_and_reported(convert: Converter) -> None:
    marked = row(cell(bookmark("_RowMark", "")), cell(""))
    result, report, _warnings = convert.reported(
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
    assert "| Name | Value |\n| --- | --- |\n| a | 1 |\n" in result
    # A template's fill-in row stays.
    assert "| Field | Notes |\n| --- | --- |\n| | |\n" in result
    assert "<table" not in result
    assert "[Back to the row](#tables)" in result
    assert "  tables: applied (2 Markdown, 4 empty row(s) omitted)" in report.summary()
    assert report.status("checks") == "applied"


def test_legacy_horizontal_merge_is_source_aware(convert: Converter, pandoc: str) -> None:
    first, continuation = cell("Merged"), cell("")
    first_props = first.find(W + "tcPr")
    continuation_props = continuation.find(W + "tcPr")
    assert first_props is not None
    assert continuation_props is not None
    ET.SubElement(first_props, W + "hMerge", {W + "val": "restart"})
    ET.SubElement(continuation_props, W + "hMerge")
    result, _stats = convert(
        [
            table(row(first, continuation, header=True), row(cell("left"), cell("right"))),
        ]
    )
    assert 'colspan="2"' in result
    assert len(list(_reference.pandoc_html(result, pandoc).iter("tr"))) == 2


def test_merged_rows_and_source_caption_survive(convert: Converter, pandoc: str) -> None:
    result, _stats = convert(
        [
            table(
                row(cell("Header", colspan=2), header=True),
                row(cell("Shared", merge="restart"), cell("one")),
                row(cell("", merge="continue"), cell("two")),
                caption="A source caption",
            ),
        ]
    )
    assert 'colspan="2"' in result
    assert 'rowspan="2"' in result
    assert "<caption>A source caption</caption>" in result
    assert len(list(_reference.pandoc_html(result, pandoc).iter("tr"))) == 3


def test_bookmark_and_parenthesized_identity(convert: Converter) -> None:
    result, stats = convert(
        [
            bookmark("_Ref_(detail)", "Destination.", "Heading3"),
            hyperlink("See it", anchor="_Ref_(detail)"),
        ]
    )
    assert "<a " not in result
    assert "[See it](#destination)" in result
    assert "### Destination.\n" in result
    assert stats["bookmarks"] == 1


def test_bookmark_matching_its_heading_id_needs_no_html_workaround(
    convert: Converter, pandoc: str
) -> None:
    result, _stats = convert(
        [
            bookmark("title", "Title", "Heading1"),
            hyperlink("Go", anchor="title"),
        ]
    )
    parsed = _validation.collect_references(_reference.pandoc_html(result, pandoc))
    assert parsed.anchors.count("title") == 1
    assert "# Title" in result
    assert "[Go](#title)" in result
    assert "<h1" not in result
    assert "<a " not in result


def test_word_bookmark_wins_over_a_different_heading_id(convert: Converter, pandoc: str) -> None:
    result, _stats = convert(
        [
            paragraph("Title", "Heading1"),
            hyperlink("Real destination", anchor="title"),
            bookmark("title", "Actual target", "Heading1"),
        ]
    )
    assert "[Real destination](#actual-target)" in result
    assert "<a " not in result
    assert _validation.collect_references(_reference.pandoc_html(result, pandoc)).anchors == [
        "title",
        "actual-target",
    ]


def test_missing_word_bookmark_is_not_replaced_by_matching_heading_slug(convert: Converter) -> None:
    result, report, warnings = convert.reported(
        [
            paragraph("Title", "Heading1"),
            hyperlink("Missing real bookmark", anchor="title"),
        ]
    )
    assert "Missing real bookmark" in result
    assert "](#title)" not in result
    assert report.status("links") == "not applied"
    assert "link to missing Word bookmark 'title'; kept as plain text" in warnings


def test_unicode_bookmark_and_output_name(convert: Converter, tmp_path: Path, pandoc: str) -> None:
    output = tmp_path / "R\u00e9sum\u00e9.v2.md"
    result, _stats = convert(
        [
            bookmark("_R\u00e9sum\u00e9", "R\u00e9sum\u00e9. 1", "Heading3"),
            hyperlink("Go", anchor="_R\u00e9sum\u00e9"),
            image(),
        ],
        output=output,
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
    )
    assert "<a " not in result
    assert "#r%C3%A9sum%C3%A9-1" in result
    assert (
        "résumé-1" in _validation.collect_references(_reference.pandoc_html(result, pandoc)).anchors
    )
    assert (tmp_path / ".images" / "Résumé.v2" / "image.png").is_file()


def test_bookmark_in_table_and_table_link(convert: Converter) -> None:
    result, _stats = convert(
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
    assert "<a " not in result
    assert "<table" not in result
    assert "| Header |" in result
    assert "[Cell](#title)" in result
    assert "[Back](#title)" in result


def test_complex_table_bookmarks_use_section_links_without_anchor_wrappers(
    convert: Converter,
) -> None:
    result, _stats = convert(
        [
            paragraph("1. Overview", "Heading1"),
            table(
                row(cell(bookmark("_Cell", "Merged target"), colspan=2)),
                row(cell(hyperlink("Return", anchor="_Cell")), cell("Data")),
            ),
            hyperlink("Go to table", anchor="_Cell"),
        ]
    )
    assert "<table" in result
    assert 'href="#1-overview"' in result
    assert "[Go to table](#1-overview)" in result
    assert 'id="_Cell"' not in result
    assert 'name="_Cell"' not in result
    assert "<span" not in result


def test_preamble_bookmark_targets_document_top(convert: Converter, tmp_path: Path) -> None:
    result, _stats = convert(
        [
            bookmark("_Before", "Opening text"),
            hyperlink("Opening", anchor="_Before"),
            paragraph("Body", "Heading1"),
            hyperlink("Top", anchor="_Before"),
        ],
        output=tmp_path / "guide.md",
    )
    assert "[Opening](#)" in result
    assert "<a " not in result
    assert "[Top](#)" in result
    assert result.startswith("Opening text")


def test_only_referenced_nonheading_bookmarks_report_retargeting(
    source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_docx(
        source,
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
    _pipeline.convert_docx(source)
    warnings = capsys.readouterr().err
    assert "'_Before' now links to the document top" in warnings
    assert "'_Body' now links to its containing section #1-overview" in warnings
    assert "_Unused" not in warnings
    assert "_Exact" not in warnings
    # A heading's bookmark leads to the heading, others to their section or the document top.
    result = source.with_suffix(".md").read_text(encoding="utf-8")
    assert "[Section](#1-overview)" in result
    assert "[Body](#1-overview)" in result
    assert "[Preamble](#)" in result


def test_links_to_repeated_headings_use_their_final_ids(convert: Converter, tmp_path: Path) -> None:
    result, _stats = convert(
        [
            paragraph("First", "Heading1"),
            bookmark("_One", "2. Same & Detail", "Heading2"),
            hyperlink("Second target", anchor="_Two"),
            paragraph("Second", "Heading1"),
            bookmark("_Two", "2. Same & Detail", "Heading2"),
            hyperlink("First target", anchor="_One"),
        ],
        output=tmp_path / "guide.md",
    )
    assert "[Second target](#2-same--detail-1)" in result
    assert "[First target](#2-same--detail)" in result


def test_markdown_caption_does_not_change_word_section_mapping(
    convert: Converter, pandoc: str
) -> None:
    result, _stats = convert(
        [
            bookmark("_Heading", "Heading", "Heading1"),
            table(row(cell("Header")), columns=1, caption="# Not a heading"),
            hyperlink("Actual section", anchor="_Heading"),
        ]
    )
    assert "<table" not in result
    parsed = _reference.pandoc_html(result, pandoc)
    assert _validation.collect_references(parsed).headings == [1]
    assert "# Not a heading" in "".join(parsed.itertext())
    assert "[Actual section](#heading)" in result


def test_link_to_missing_bookmark_is_kept_as_text_and_summarized(
    convert: Converter, tmp_path: Path
) -> None:
    output = tmp_path / "output.md"
    output.write_text("old", encoding="utf-8")
    result, report, _warnings = convert.reported(
        [hyperlink("Missing", anchor="_Missing")], output=output
    )
    assert result == "Missing\n"
    summary = report.summary()
    assert summary[0] == "docx-to-md: transforms"
    assert "  links: not applied (1)" in summary
    assert "    not applied (1): link to a missing Word bookmark; kept as plain text" in summary
    assert "  checks: passed" in summary
    assert "_Missing" not in "\n".join(summary)


def test_cross_references_to_missing_bookmarks_are_kept_as_text(convert: Converter) -> None:
    def reference(anchor: str, value: str, *, simple: bool = False) -> ET.Element:
        result = paragraph("See ")
        result.extend(page_reference(anchor, value, simple=simple))
        return result

    result, report, warnings = convert.reported(
        [
            bookmark("_Target", "Target", "Heading1"),
            table(
                row(cell(reference("_Missing", "3")), cell(reference("_Target", "4"))),
                row(cell(reference("_Gone", "5", simple=True)), cell("Plain")),
            ),
            reference("_Missing", "6"),
        ]
    )
    # A page number stays text, whether its bookmark exists or not.
    assert "| See 3 | See 4 |" in result
    assert "| See 5 | Plain |" in result
    assert "See 6" in result
    assert dict(report.records["links"].skipped) == {
        "link to a missing Word bookmark; kept as plain text": 3
    }
    assert "links: cross-reference to missing Word bookmark '_Gone'; kept as plain text" in warnings
    assert report.status("checks") == "applied"


def test_simple_fields_keep_their_cached_results(convert: Converter) -> None:
    def field(instruction: str, *children: ET.Element) -> ET.Element:
        result = ET.Element(W + "fldSimple", {W + "instr": instruction})
        result.extend(children)
        return result

    def line(*parts: str | ET.Element) -> ET.Element:
        result = ET.Element(W + "p")
        result.extend(_ooxml.word_run(part) if isinstance(part, str) else part for part in parts)
        return result

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
    result, report, _warnings = convert.reported(body)
    # A cross-reference with \h is a hyperlink in Word, to the section that holds its bookmark.
    for text in ("Table 2-1: Registers", "Figure 3: Pipeline", "See [Figure 3](#results)."):
        assert f"\n{text}\n" in result
    assert report.status("fields") == "applied"
    assert "  fields: applied (4 simple field(s) kept)" in report.summary()
    assert "[Back to the figure](#results)" in result
    assert not report.records["bookmarks"].skipped
    assert report.status("checks") == "applied"
    assert not report.records["checks"].skipped


def test_bookmarks_in_leading_title_paragraphs_keep_their_position(convert: Converter) -> None:
    # Pandoc reads leading Title and Subtitle paragraphs as metadata, outside its body.
    result, report, warnings = convert.reported(
        [
            paragraph(""),
            bookmark("_Hlk1", "Document title", "Title"),
            paragraph("Document subtitle", "Subtitle"),
            paragraph("Body"),
            hyperlink("Back to the title", anchor="_Hlk1"),
        ],
        styles=(word_style("Title"), word_style("Subtitle")),
    )
    assert report.status("checks") == "applied"
    assert not report.records["checks"].skipped
    assert report.status("bookmarks") == "applied"
    assert re.search(
        r"Document title\n\nDocument subtitle\n\nBody\n\n\[Back to the title\]\(#", result
    )
    assert "checks:" not in warnings


def test_word_line_breaks_are_hard_line_breaks(convert: Converter) -> None:
    # A Markdown heading is one line, so a line break in one is a space.
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

    result, report, _warnings = convert.reported(
        [
            broken("first line", "second line"),
            broken("Heading top", "Heading bottom", style="Heading1"),
            # Breaks that start or end a heading, as above a cover page's title, show nothing.
            broken("", "", "Edges", "", style="Heading1"),
            broken(image("art"), hyperlink("Source", relation="site")),
        ],
        relations=(
            ("art", "image", "media/art.png", "Internal"),
            ("site", "hyperlink", "https://example.test/a?b=1&c=2", "External"),
        ),
        media={"art.png": PNG},
    )
    assert "first line\\\nsecond line\n" in result
    assert "# Heading top Heading bottom\n" in result
    assert "\n# Edges\n" in result
    assert re.search(
        r"!\[Diagram\]\(\.images/[^)\n]+\)\\\n\[Source\]\(https://example\.test/a\?b=1&amp;c=2\)",
        result,
    )
    assert (
        "    not applied (1): Word line break in a heading; a Markdown heading is one line"
        in report.summary()
    )
    assert report.status("checks") == "applied"
    assert not report.records["checks"].skipped


def test_lines_of_dashes_or_equals_signs_stay_text(convert: Converter) -> None:
    # Pandoc writes them as they are, which Markdown reads as a rule or a heading underline.
    def broken(*texts: str) -> ET.Element:
        result = ET.Element(W + "p")
        for index, text in enumerate(texts):
            if index:
                ET.SubElement(ET.SubElement(result, W + "r"), W + "br")
            result.append(_ooxml.word_run(text))
        return result

    # An empty paragraph with a bottom border, as Word draws a line typed as ---.
    rule = ET.Element(W + "p")
    borders = ET.SubElement(ET.SubElement(rule, W + "pPr"), W + "pBdr")
    ET.SubElement(borders, W + "bottom", {W + "val": "single"})
    result, _stats = convert(
        [
            paragraph("Cache", "Title"),
            paragraph("Section", "Heading1"),
            paragraph("---"),
            broken("Lead", "==="),
            broken("Lead", "---"),
            rule,
            paragraph("End"),
        ]
    )
    assert result.endswith("\\---\n\nLead\\\n\\===\n\nLead\\\n\\---\n\n---\n\nEnd\n")


def test_line_breaks_at_block_ends_leave_no_backslash(convert: Converter) -> None:
    def trailing(content: ET.Element) -> ET.Element:
        ET.SubElement(ET.SubElement(content, W + "r"), W + "br")
        return content

    result, report, warnings = convert.reported(
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
    assert "\\" not in result
    assert re.search(r"7\.\s+Alpha\n+8\.\s+Beta\n\nPlain\n\nMarked\n", result)
    assert report.status("lists") == "applied"
    assert "<ol" not in result
    assert "lists:" not in warnings


def test_breaks_at_paragraph_edges_go_and_a_blank_line_starts_a_paragraph(
    convert: Converter,
) -> None:
    def line(*parts: str | None, bold: bool = False) -> ET.Element:
        """A paragraph of *parts*, None being a line break."""
        result = ET.Element(W + "p")
        for part in parts:
            run = ET.SubElement(result, W + "r")
            if bold:
                ET.SubElement(ET.SubElement(run, W + "rPr"), W + "b")
            if part is None:
                ET.SubElement(run, W + "br")
            else:
                ET.SubElement(run, W + "t", {_ooxml.XML_SPACE: "preserve"}).text = part
        return result

    result, _stats = convert(
        [
            line("Two after", None, None),
            line(None, "One before"),
            line("Bold", None, bold=True),
            line("Above", None, None, "below", None, "next"),
            table(
                row(cell("h1"), cell("h2")), row(cell(line("in", None, None, "cell")), cell("x"))
            ),
        ]
    )
    # A break at a paragraph's end would show as a backslash.
    assert result == (
        "Two after\n\nOne before\n\n**Bold**\n\nAbove\n\nbelow\\\nnext\n\n"
        "| h1 | h2 |\n| --- | --- |\n| in<br><br>cell | x |\n"
    )


@pytest.mark.parametrize(
    "simple", [pytest.param(False, id="complex"), pytest.param(True, id="simple")]
)
def test_page_references_keep_their_number_as_text(simple: bool, convert: Converter) -> None:
    # A page number means nothing in Markdown; Pandoc would make it a link.
    line = paragraph("See page ")
    line.extend(page_reference("_Ref1", "5", simple=simple))
    line.append(_ooxml.word_run("."))
    result, report, _warnings = convert.reported([bookmark("_Ref1", "Target", "Heading1"), line])
    assert result == "# Target\n\nSee page 5.\n"
    assert "  fields: applied (1 page number(s) kept as text" in "\n".join(report.summary())


def test_horizontal_lines_are_rules(convert: Converter) -> None:
    def line(*text: str) -> ET.Element:
        result = paragraph(*text)
        properties = ET.SubElement(result, W + "pPr")
        ET.SubElement(properties, W + "jc", {W + "val": "center"})
        result.remove(properties)
        result.insert(0, properties)
        run = ET.SubElement(result, W + "r")
        ET.SubElement(run, W + "rPr")
        shape = ET.SubElement(ET.SubElement(run, W + "pict"), _ooxml.VML + "rect")
        shape.set(_ooxml.OFFICE + "hr", "t")
        return result

    result, report, warnings = convert.reported(
        [paragraph("Above"), line(), paragraph("Between"), line("Beside"), paragraph("Below")]
    )
    assert result == "Above\n\n---\n\nBetween\n\nBeside\n\nBelow\n"
    assert "1 horizontal line(s) kept" in "\n".join(report.summary())
    assert "horizontal line at paragraph 4: no embedded image; omitted" in warnings


def test_a_footnote_keeps_its_link(convert: Converter) -> None:
    # Pandoc reads a note's r:id with the prefixes that the document part declares.
    noted = paragraph("Text")
    ET.SubElement(ET.SubElement(noted, W + "r"), W + "footnoteReference", {W + "id": "1"})
    notes = ET.Element(W + "footnotes")
    note = ET.SubElement(notes, W + "footnote", {W + "id": "1"})
    note.append(hyperlink("Spec", relation="spec"))
    note_relations = (
        f'<Relationships xmlns="{_ooxml.PACKAGE_REL[1:-1]}"><Relationship Id="spec" '
        f'Type="{R[1:-1]}/hyperlink" Target="https://example.test/spec" '
        'TargetMode="External"/></Relationships>'
    )
    result, _stats = convert(
        [noted],
        relations=(("notes", "footnotes", "footnotes.xml", "Internal"),),
        extras={
            "word/footnotes.xml": _ooxml.word_xml_bytes(notes),
            "word/_rels/footnotes.xml.rels": note_relations.encode(),
        },
    )
    assert result == "Text[^1]\n\n[^1]: [Spec](https://example.test/spec)\n"


@pytest.mark.parametrize(
    ("math", "inline", "display"),
    [
        pytest.param("github", "$`x + 1`$", "``` math\nx + 1\n```", id="github"),
        pytest.param("dollars", "$x + 1$", "$$x + 1$$", id="dollars"),
    ],
)
def test_equations_are_written_in_the_chosen_syntax(
    math: Literal["github", "dollars"], inline: str, display: str, source: Path
) -> None:
    def equation(*, alone: bool = False) -> ET.Element:
        formula = ET.Element(M + "oMath")
        ET.SubElement(ET.SubElement(formula, M + "r"), M + "t").text = "x+1"
        if not alone:
            return formula
        block = ET.Element(M + "oMathPara")
        block.append(formula)
        return block

    def line(*parts: str | ET.Element) -> ET.Element:
        result = ET.Element(W + "p")
        result.extend(_ooxml.word_run(part) if isinstance(part, str) else part for part in parts)
        return result

    noted = line("Noted")
    ET.SubElement(ET.SubElement(noted, W + "r"), W + "footnoteReference", {W + "id": "1"})
    notes = ET.Element(W + "footnotes")
    ET.SubElement(notes, W + "footnote", {W + "id": "1"}).append(line("See ", equation()))
    write_docx(
        source,
        [
            paragraph("Section", "Heading1"),
            line("Inline ", equation(), "."),
            line(equation(alone=True)),
            table(row(cell("Name"), cell("Value")), row(cell("a"), cell(line(equation())))),
            noted,
        ],
        relations=(("notes", "footnotes", "footnotes.xml", "Internal"),),
        extras={"word/footnotes.xml": _ooxml.word_xml_bytes(notes)},
    )
    _pipeline.convert_docx(source, math=math)
    page = source.with_suffix(".md").read_text(encoding="utf-8")
    assert f"Inline {inline}.\n\n{display}\n" in page
    assert f"| a | {inline} |" in page
    assert f"[^1]: See {inline}\n" in page


def test_equation_text_that_pandoc_cannot_read_back_is_kept_without_a_warning(
    convert: Converter,
) -> None:
    # A \_ typed in an equation is a literal run; in bold italic, Pandoc writes it as
    # \textit{\textbf{\_}}, which its own TeX reader cannot read.
    formula = ET.Element(M + "oMath")
    for text, literal in (("a", False), ("_", True), ("b", False)):
        run = ET.SubElement(formula, M + "r")
        properties = ET.SubElement(run, M + "rPr")
        if literal:
            ET.SubElement(properties, M + "lit")
        ET.SubElement(properties, M + "sty", {M + "val": "bi"})
        ET.SubElement(run, M + "t").text = text
    block = ET.Element(M + "oMathPara")
    block.append(formula)
    equation = ET.Element(W + "p")
    equation.append(block)

    result, _report, warnings = convert.reported([paragraph("Section", "Heading1"), equation])

    assert "\\textit{\\textbf{\\_}}" in result
    assert warnings == ""


def test_an_image_in_a_footnote_is_published(convert: Converter, tmp_path: Path) -> None:
    noted = paragraph("Text")
    ET.SubElement(ET.SubElement(noted, W + "r"), W + "footnoteReference", {W + "id": "1"})
    notes = ET.Element(W + "footnotes")
    ET.SubElement(notes, W + "footnote", {W + "id": "1"}).append(image("art", alt="Logo"))
    note_relations = (
        f'<Relationships xmlns="{_ooxml.PACKAGE_REL[1:-1]}"><Relationship Id="art" '
        f'Type="{R[1:-1]}/image" Target="media/art.png"/></Relationships>'
    )
    result, stats = convert(
        [noted],
        relations=(("notes", "footnotes", "footnotes.xml", "Internal"),),
        media={"art.png": PNG},
        extras={
            "word/footnotes.xml": _ooxml.word_xml_bytes(notes),
            "word/_rels/footnotes.xml.rels": note_relations.encode(),
        },
    )
    assert re.fullmatch(
        r"Text\[\^1\]\n\n\[\^1\]: !\[Logo\]\(\.images/Synthetic%20Guide/[^)]+\.png\)\n", result
    )
    assert stats["image_occurrences"] == 1
    assert [path.read_bytes() for path in (tmp_path / ".images" / "Synthetic Guide").iterdir()] == [
        PNG
    ]


def test_check_box_form_fields_show_their_state(convert: Converter) -> None:
    def check_box(*settings: tuple[str, str | None]) -> list[ET.Element]:
        begin = ET.Element(W + "r")
        mark = ET.SubElement(begin, W + "fldChar", {W + "fldCharType": "begin"})
        box = ET.SubElement(ET.SubElement(mark, W + "ffData"), W + "checkBox")
        for name, value in settings:
            ET.SubElement(box, W + name, {} if value is None else {W + "val": value})
        instruction = ET.Element(W + "r")
        ET.SubElement(instruction, W + "instrText").text = " FORMCHECKBOX "
        end = ET.Element(W + "r")
        ET.SubElement(end, W + "fldChar", {W + "fldCharType": "end"})
        return [begin, instruction, end]

    line = paragraph("Done ")
    line.extend(check_box(("default", "0"), ("checked", None)))
    line.append(_ooxml.word_run(" Open "))
    line.extend(check_box(("default", "1"), ("checked", "0")))
    line.append(_ooxml.word_run(" Default "))
    line.extend(check_box(("default", "1")))
    result, report, _warnings = convert.reported([line])
    # Word draws a form field's box; it has no text for Pandoc to keep.
    assert result == "Done \u2612 Open \u2610 Default \u2612\n"
    assert "  fields: applied (3 check box(es) shown)" in report.summary()


def test_every_kind_of_underline_is_underline(convert: Converter) -> None:
    def underlined(text: str, kind: str) -> ET.Element:
        return formatted(_ooxml.word_run(text), "u", kind)

    result, _stats = convert(
        [
            paragraph_of(
                underlined("single", "single"),
                _ooxml.word_run(" "),
                underlined("double", "double"),
                _ooxml.word_run(" "),
                underlined("wavy", "wave"),
                _ooxml.word_run(" "),
                underlined("none", "none"),
            )
        ]
    )
    assert result == "<u>single</u> <u>double</u> <u>wavy</u> none\n"


def test_stray_field_marks_are_removed(convert: Converter) -> None:
    stray = paragraph("Before")
    ET.SubElement(ET.SubElement(stray, W + "r"), W + "fldChar", {W + "fldCharType": "end"})
    result, report, _warnings = convert.reported([stray, paragraph("After")])
    assert "Before\n\nAfter" in result
    assert "  fields: applied (1 stray field mark(s) removed)" in report.summary()


def test_nested_empty_and_targetless_hyperlinks(convert: Converter) -> None:
    nested = hyperlink("Outer ", relation="outer")
    outer = nested.find(W + "hyperlink")
    assert outer is not None
    ET.SubElement(outer, W + "hyperlink", {R + "id": "inner"}).append(_ooxml.word_run("Inner"))
    result, report, warnings = convert.reported(
        [nested, hyperlink(" ", relation="outer"), hyperlink("Plain")],
        relations=(
            ("outer", "hyperlink", "https://example.test/outer", "External"),
            ("inner", "hyperlink", "https://example.test/inner", "External"),
        ),
    )
    assert result == "[Outer Inner](https://example.test/outer)\n\nPlain\n"
    summary = report.summary()
    assert "  links: partial (1 applied, 1 not applied)" in summary
    assert (
        "    not applied (1): Word hyperlink nested in another hyperlink; "
        "its text stays in the outer link" in summary
    )
    assert warnings.count("links:") == 1


def test_underline_stays_but_not_in_links_headings_or_code(convert: Converter) -> None:
    def underlined(node: ET.Element) -> ET.Element:
        for run in node.iter(W + "r"):
            formatted(run, "u", "single")
        return node

    body = paragraph_of(
        _ooxml.word_run("Do "),
        formatted(_ooxml.word_run("not"), "u", "single"),
        _ooxml.word_run(" connect."),
    )
    code = paragraph_of(
        _ooxml.word_run("Call "),
        formatted(code_run("init()"), "u", "single"),
        _ooxml.word_run(" first."),
    )
    must = paragraph_of(_ooxml.word_run("Must "), formatted(_ooxml.word_run("not"), "u", "single"))
    grid = table(
        row(cell("Rule"), cell("Link"), header=True),
        row(cell(must), cell(underlined(hyperlink("Site", relation="site")))),
    )
    result, report, _warnings = convert.reported(
        [
            underlined(paragraph("Overview", "Heading1")),
            body,
            underlined(hyperlink("Spec site", relation="site")),
            code,
            grid,
        ],
        relations=(("site", "hyperlink", "https://example.test/spec", "External"),),
    )
    assert (
        result
        == "# Overview\n\nDo <u>not</u> connect.\n\n[Spec site](https://example.test/spec)\n\n"
        "Call `init()` first.\n\n| Rule | Link |\n| --- | --- |\n"
        "| Must <u>not</u> | [Site](https://example.test/spec) |\n"
    )
    assert "  underline: applied (4)" in report.summary()


def test_screen_tips_become_link_titles_and_double_strikethrough_is_struck(
    convert: Converter,
) -> None:
    tipped = hyperlink("Spec site", relation="site")
    link = tipped.find(W + "hyperlink")
    assert link is not None
    link.set(W + "tooltip", 'Opens the "specification"')
    struck = paragraph_of(
        formatted(_ooxml.word_run("double"), "dstrike"),
        _ooxml.word_run(" and "),
        formatted(_ooxml.word_run("single"), "strike"),
    )
    result, _report, _warnings = convert.reported(
        [tipped, struck],
        relations=(("site", "hyperlink", "https://example.test/spec", "External"),),
    )
    assert (
        result == '[Spec site](https://example.test/spec "Opens the \\"specification\\"")\n\n'
        "~~double~~ and ~~single~~\n"
    )


def test_hyperlink_with_a_missing_relationship_still_converts(convert: Converter) -> None:
    result, report, warnings = convert.reported(
        [paragraph("Before"), hyperlink("Dangling", relation="gone")]
    )
    assert result == "Before\n\nDangling\n"
    assert dict(report.records["links"].skipped) == {
        "Word hyperlink relationship is missing; its target is dropped": 1
    }
    assert "links: Word hyperlink relationship 'gone' is missing; its target is dropped" in warnings


def test_a_note_reference_in_a_hyperlink_follows_the_link(convert: Converter) -> None:
    content = hyperlink("Label", relation="site")
    link = content.find(W + "hyperlink")
    assert link is not None
    ET.SubElement(ET.SubElement(link, W + "r"), W + "footnoteReference", {W + "id": "1"})
    notes = (
        f'<w:footnotes xmlns:w="{W[1:-1]}"><w:footnote w:id="1"><w:p><w:r>'
        "<w:t>Note</w:t></w:r></w:p></w:footnote></w:footnotes>"
    )
    result, report, _warnings = convert.reported(
        [content],
        relations=(
            ("site", "hyperlink", "https://example.test/a", "External"),
            ("notes", "footnotes", "footnotes.xml", "Internal"),
        ),
        extras={"word/footnotes.xml": notes.encode()},
    )
    # Markdown cannot hold a footnote reference in link text.
    assert result == "[Label](https://example.test/a)[^1]\n\n[^1]: Note\n"
    assert report.status("links") == "applied"
    assert report.status("checks") == "applied"


def test_png_and_svg_do_not_require_office_or_poppler(
    convert: Converter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = executables.which

    def forbid_optional(name: str, path: str | None = None) -> str | None:
        if name in {"libreoffice", "soffice", "pdftocairo", "pdfinfo", "pdftotext"}:
            pytest.fail(f"Unnecessary optional dependency probe: {name}")
        return original(name, path)

    def forbid_office() -> str | None:
        pytest.fail("Unnecessary optional dependency probe: LibreOffice")

    monkeypatch.setattr(executables, "which", forbid_optional)
    monkeypatch.setattr(executables, "libreoffice", forbid_office)
    result, stats = convert(
        [image("png"), image("svg")],
        relations=(
            ("png", "image", "media/image.png", "Internal"),
            ("svg", "image", "media/image.svg", "Internal"),
        ),
        media={"image.png": PNG, "image.svg": SVG},
    )
    assert ".images/Synthetic%20Guide/image.png" in result
    assert ".images/Synthetic%20Guide/image.svg" in result
    assert stats["image_occurrences"] == 2
    assert (tmp_path / ".images" / "Synthetic Guide" / "image.png").read_bytes() == PNG


def test_line_breaks_in_list_items_formatting_and_links(convert: Converter) -> None:
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
    result, report, _warnings = convert.reported(
        [item, numbered("Beta"), bold, link],
        relations=(("site", "hyperlink", "https://example.test/", "External"),),
        extras={"word/numbering.xml": numbering_xml()},
    )
    # Link text is one line; the list stays tight.
    assert (
        result == "7. Alpha\\\n   continued\n8. Beta\n\n**Bold\\\ntext**\n\n"
        "[Line one Line two](https://example.test/)\n"
    )
    assert report.status("lists") == "applied"
    assert report.status("checks") == "applied"


@pytest.mark.parametrize(
    ("kind", "start", "expected"),
    [
        pytest.param("bullet", 1, "- First\n- Second\n  - Nested\n- Third\n", id="bullets"),
        pytest.param("decimal", 1, "1. First\n2. Second\n   1. Nested\n3. Third\n", id="numbers"),
        # A nested list numbered from other than 1 cannot follow a paragraph in a tight list.
        pytest.param(
            "decimal",
            7,
            "7. First\n\n8. Second\n\n   7. Nested\n\n9. Third\n",
            id="nested-from-7",
        ),
    ],
)
def test_lists_of_one_paragraph_items_are_tight_where_markdown_allows(
    convert: Converter, kind: str, start: int, expected: str
) -> None:
    result, _stats = convert(
        [
            numbered("First"),
            numbered("Second"),
            numbered("Nested", 1),
            numbered("Third"),
            paragraph("After"),
        ],
        extras={"word/numbering.xml": numbering_xml(kind, start)},
    )
    assert result == expected + "\nAfter\n"


def test_list_markers_take_one_space_and_lines_have_no_trailing_whitespace(
    convert: Converter,
) -> None:
    result, report, _warnings = convert.reported(
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
    assert (
        result == "[Go](#scope)\n\n# Scope\n\nText ends with spaces\n\n"
        "7. Alpha\n8.\n9. Beta\n\nOutro\n"
    )
    assert "  spaces: applied (3)" in report.summary()
    assert "  cleanup: applied (1 line(s) with trailing whitespace trimmed)" in report.summary()
    assert report.status("checks") == "applied"


def test_empty_headings_are_omitted(convert: Converter) -> None:
    report = _diagnostics.TransformReport()
    result, _stats = convert(
        [
            paragraph("One", "Heading1"),
            paragraph("", "Heading2"),
            paragraph("Text"),
            paragraph("Two", "Heading1"),
            paragraph("\u00a0", "Heading2"),
            paragraph("End"),
        ],
        qualify=True,
        report=report,
    )
    assert report.status("duplicate-headings") == "not needed"
    assert "    not applied (2): Word heading has no text; omitted" in report.summary()
    assert result == "# One\n\nText\n\n# Two\n\nEnd\n"


def test_footnotes_are_numbered_in_order_and_defined_at_the_end(convert: Converter) -> None:
    def noted(text: str, number: int) -> ET.Element:
        result = paragraph(text)
        ET.SubElement(
            ET.SubElement(result, W + "r"), W + "footnoteReference", {W + "id": str(number)}
        )
        return result

    notes = ET.Element(W + "footnotes")
    for number, text in ((1, "First note"), (2, "Cell note"), (3, "Second note")):
        note = ET.SubElement(notes, W + "footnote", {W + "id": str(number)})
        note.append(paragraph(text))
    result, _stats = convert(
        [
            paragraph("One", "Heading1"),
            noted("Text", 1),
            table(row(cell(noted("Cell", 2)), cell("x")), row(cell("a"), cell("b"))),
            paragraph("Two", "Heading1"),
            noted("More", 3),
        ],
        relations=(("notes", "footnotes", "footnotes.xml", "Internal"),),
        extras={"word/footnotes.xml": _ooxml.word_xml_bytes(notes)},
    )
    assert result == (
        "# One\n\nText[^1]\n\n| Cell[^2] | x |\n| --- | --- |\n| a | b |\n\n"
        "# Two\n\nMore[^3]\n\n[^1]: First note\n\n[^2]: Cell note\n\n[^3]: Second note\n"
    )


def test_bookmark_on_an_empty_heading_goes_to_the_heading_after_it(convert: Converter) -> None:
    # Enter at the start of a heading leaves its table-of-contents bookmark in the
    # empty heading above it.
    result, report, _warnings = convert.reported(
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
    assert (
        result == "[2.1 Interfaces](#interfaces)\n\n[Notes](#interfaces)\n\n# Design\n\nText\n\n"
        "## Interfaces\n\nPorts\n\nA note under Interfaces\n"
    )
    assert "    not applied (3): Word heading has no text; omitted" in report.summary()
    assert report.status("checks") == "applied"
    assert not report.records["checks"].skipped


def test_bookmark_in_an_empty_paragraph_goes_to_the_heading_after_it(convert: Converter) -> None:
    result, report, warnings = convert.reported(
        [
            hyperlink("Figure 1: Pipe", anchor="_Toc5"),
            paragraph("Previous", "Heading1"),
            paragraph("Text"),
            bookmark("_Toc5", ""),
            paragraph("Buffers", "Heading1"),
            paragraph("Figure 1: Pipe"),
        ]
    )
    assert "[Figure 1: Pipe](#buffers)" in result
    assert report.status("checks") == "applied"
    assert "_Toc5" not in warnings


def test_heading_with_only_a_picture_is_a_paragraph(convert: Converter) -> None:
    picture = image()
    properties = ET.Element(W + "pPr")
    ET.SubElement(properties, W + "pStyle", {W + "val": "Heading3"})
    picture.insert(0, properties)
    picture.insert(1, ET.Element(W + "bookmarkStart", {W + "id": "9", W + "name": "_Toc9"}))
    picture.append(ET.Element(W + "bookmarkEnd", {W + "id": "9"}))
    result, report, _warnings = convert.reported(
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
    assert (
        result == "[Figure 1: Diagram](#block-diagram)\n\n## Block diagram\n\n"
        "![Diagram](.images/Synthetic%20Guide/image.png)\n\nFigure 1: Diagram\n\n## Next\n"
    )
    assert (
        "    not applied (1): Word heading has only a picture; kept as a paragraph"
        in report.summary()
    )
    assert report.status("images") == "applied"
    assert report.status("checks") == "applied"


def test_word_title_becomes_the_only_h1(convert: Converter) -> None:
    result, report, _warnings = convert.reported(
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
    assert (
        result == "# XYZ_TOP Guide \\#2\n\nDate: today\n\n[Go to setup](#setup)\n\n## Intro\n\n"
        "Text\n\n### Setup\n\nMore\n"
    )
    assert "  title: applied (2 heading(s) moved down one level)" in report.summary()
    assert report.status("checks") == "applied"


def test_a_level_6_heading_under_the_word_title_becomes_a_paragraph(convert: Converter) -> None:
    result, report, warnings = convert.reported(
        [
            paragraph("Guide Title", "Title"),
            *(paragraph(f"Level {level}", f"Heading{level}") for level in range(1, 6)),
            bookmark("_Deep", "Level 6", "Heading6"),
            paragraph("Text"),
            hyperlink("Go deep", anchor="_Deep"),
        ],
        styles=(word_style("Title"),),
    )
    assert result.startswith(
        "# Guide Title\n\n## Level 1\n\n### Level 2\n\n#### Level 3\n\n##### Level 4\n\n"
        "###### Level 5\n\nLevel 6\n\nText\n"
    )
    # The paragraph's bookmark leads to its section.
    assert "[Go deep](#level-5)" in result
    assert (
        "  title: partial (5 heading(s) moved down one level applied, 1 not applied)"
        in report.summary()
    )
    assert (
        "title: Word heading 'Level 6': a level 6 heading has no Markdown level left below "
        "the title; kept as a paragraph" in warnings
    )
    assert report.status("checks") == "applied"


def test_a_word_title_and_a_heading_of_the_same_text_keep_their_ids(convert: Converter) -> None:
    result, report, _warnings = convert.reported(
        [
            paragraph("Guide Title", "Title"),
            bookmark("_Same", "Guide Title", "Heading1"),
            paragraph("Text"),
            hyperlink("Back", anchor="_Same"),
        ],
        styles=(word_style("Title"),),
    )
    assert result == "# Guide Title\n\n## Guide Title\n\nText\n\n[Back](#guide-title-1)\n"
    assert "  title: applied (1 heading(s) moved down one level)" in report.summary()
    assert report.status("checks") == "applied"


def test_a_word_title_after_the_first_heading_stays_a_paragraph(convert: Converter) -> None:
    result, report, _warnings = convert.reported(
        [paragraph("Intro", "Heading1"), paragraph("Late Title", "Title"), paragraph("Text")],
        styles=(word_style("Title"),),
    )
    assert result == "# Intro\n\nLate Title\n\nText\n"
    assert (
        "    not applied (1): the Word title is not a paragraph before the first heading; "
        "kept as written" in report.summary()
    )


def test_duplicate_qualification_leaves_out_the_word_title(convert: Converter) -> None:
    result, _stats = convert(
        [
            paragraph("Guide", "Title"),
            paragraph("Block", "Heading1"),
            paragraph("Same", "Heading2"),
            paragraph("Same", "Heading2"),
        ],
        styles=(word_style("Title"),),
        qualify=True,
    )
    assert result == (
        "# Guide\n\n## Block\n\n### Block - Same - occurrence 1\n\n"
        "### Block - Same - occurrence 2\n"
    )


def test_images_in_table_cells_keep_a_markdown_table(convert: Converter) -> None:
    result, report, _warnings = convert.reported(
        [table(row(cell("Diagram"), header=True), row(cell(image())), columns=1)],
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
    )
    assert result == "| Diagram |\n| --- |\n| ![Diagram](.images/Synthetic%20Guide/image.png) |\n"
    assert "  tables: applied (1 Markdown)" in report.summary()


def test_missing_alt_text_is_empty_and_reported(
    convert: Converter, capsys: pytest.CaptureFixture[str]
) -> None:
    result, stats = convert(
        [image(alt=None)],
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
    )
    assert stats["unresolved_alt_text"] == 1
    assert "Unresolved alt text" in capsys.readouterr().err
    assert "![](.images/Synthetic%20Guide/image.png)" in result


def test_a_document_without_images_gets_no_image_directory(
    convert: Converter, source: Path, tmp_path: Path
) -> None:
    output = tmp_path / "out" / "Guide.md"
    convert(
        [image(alt="Diagram")],
        output=output,
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
    )
    assert (output.parent / ".images" / "Guide" / "image.png").is_file()
    write_docx(source, [paragraph("Text only")])
    _pipeline.convert_docx(source, output, replace_generated=True)
    assert sorted(path.name for path in output.parent.iterdir()) == ["Guide.md"]


def test_caption_alt_text_is_recovered_without_invention(convert: Converter, pandoc: str) -> None:
    result, _stats = convert(
        [image(alt=None), paragraph("Figure 2-1: Request flow", "Caption")],
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
    )
    assert next(_reference.pandoc_html(result, pandoc).iter("img")).get("alt") == "Request flow"


def test_caption_replaces_word_alt_text_and_is_reported(convert: Converter) -> None:
    boxed = paragraph("Note")
    boxed.extend(list(text_box("Box label", legacy=True)))
    result, report, _warnings = convert.reported(
        [
            image(alt="Generated description"),
            paragraph("Figure 3: Request flow", "Caption"),
            boxed,
        ],
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
    )
    assert "![Request flow](.images/" in result
    assert (
        "  images: partial (1 applied, 1 image(s) described by their caption, 1 not applied)"
        in report.summary()
    )


def test_an_image_below_a_heading_has_no_alt_text_of_its_own(convert: Converter) -> None:
    # The heading above is not alt text, also when Word numbers it and it stays a list item.
    outlined = numbered("Trackers")
    properties = outlined.find(W + "pPr")
    assert properties is not None
    ET.SubElement(properties, W + "outlineLvl", {W + "val": "1"})
    result, report, warnings = convert.reported(
        [
            paragraph("Block diagram", "Heading1"),
            paragraph(""),
            image(alt=None),
            outlined,
            image(alt=None),
        ],
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
        extras={"word/numbering.xml": numbering_xml()},
    )
    assert result.count("![](.images/") == 2
    assert "  images: applied (2)" in report.summary()
    assert warnings.count("Unresolved alt text") == 2


def test_repeated_and_nested_media_keep_exact_occurrences(convert: Converter, pandoc: str) -> None:
    result, stats = convert(
        [image(), table(row(cell(image(), "Table picture")), columns=1), image()],
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
    )
    assert len(list(_reference.pandoc_html(result, pandoc).iter("img"))) == 3
    assert stats["unique_images"] == 1


def test_linked_image_with_literal_alt_characters_survives(convert: Converter, pandoc: str) -> None:
    link = hyperlink("", relation="external")
    hyperlink_node = link.find(W + "hyperlink")
    assert hyperlink_node is not None
    hyperlink_node.clear()
    hyperlink_node.set(R + "id", "external")
    for run in image(alt=r"A [B] | C \ D").findall(W + "r"):
        hyperlink_node.append(run)
    result, _stats = convert(
        [link],
        relations=(
            ("image", "image", "media/image.png", "Internal"),
            ("external", "hyperlink", "https://example.test/target", "External"),
        ),
        media={"image.png": PNG},
    )
    parsed = _validation.collect_references(_reference.pandoc_html(result, pandoc))
    assert parsed.links == ["https://example.test/target"]
    assert next(_reference.pandoc_html(result, pandoc).iter("img")).get("alt") == r"A [B] | C \ D"


def test_multiple_images_and_text_in_one_word_run_are_not_dropped(convert: Converter) -> None:
    content = ET.Element(W + "p")
    run = ET.SubElement(content, W + "r")
    ET.SubElement(run, W + "t").text = "Before "
    for _ in range(2):
        drawing = image().find(f"./{W}r/{W}drawing")
        assert drawing is not None
        run.append(drawing)
    ET.SubElement(run, W + "t").text = " After"
    result, stats = convert(
        [content],
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
    )
    assert "Before" in result
    assert "After" in result
    assert stats["image_occurrences"] == 2


def test_native_svg_extension_is_preferred_over_raster_fallback(convert: Converter) -> None:
    content = image("png")
    blip = content.find(f".//{_ooxml.DRAWING}blip")
    assert blip is not None
    extension = ET.SubElement(
        ET.SubElement(blip, _ooxml.DRAWING + "extLst"), _ooxml.DRAWING + "ext"
    )
    ET.SubElement(extension, _ooxml.SVG_DRAWING + "svgBlip", {R + "embed": "svg"})
    result, stats = convert(
        [content],
        relations=(
            ("png", "image", "media/fallback.png", "Internal"),
            ("svg", "image", "media/vector.svg", "Internal"),
        ),
        media={"fallback.png": PNG, "vector.svg": SVG},
    )
    assert "vector.svg" in result
    assert "fallback.png" not in result
    assert stats["unique_images"] == 1


def test_a_word_svg_with_script_takes_its_raster_fallback(convert: Converter) -> None:
    content = image("png")
    blip = content.find(f".//{_ooxml.DRAWING}blip")
    assert blip is not None
    extension = ET.SubElement(
        ET.SubElement(blip, _ooxml.DRAWING + "extLst"), _ooxml.DRAWING + "ext"
    )
    ET.SubElement(extension, _ooxml.SVG_DRAWING + "svgBlip", {R + "embed": "svg"})
    scripted = SVG.replace(b"<path", b"<script>alert(1)</script><path")
    result, report, warnings = convert.reported(
        [content],
        relations=(
            ("png", "image", "media/fallback.png", "Internal"),
            ("svg", "image", "media/vector.svg", "Internal"),
        ),
        media={"fallback.png": PNG, "vector.svg": scripted},
    )
    assert "fallback.png" in result
    assert "vector.svg" not in result
    # The fallback worked out: a detail, which only --verbose shows.
    assert any("SVG contains script" in finding for finding in report.findings)
    assert "SVG contains script" not in warnings


@pytest.mark.parametrize("kind", ["pict", "object"])
def test_legacy_picture_and_object_previews_keep_metadata(
    kind: str, convert: Converter, pandoc: str
) -> None:
    result, stats = convert(
        [legacy_image(kind)],
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
    )
    assert stats["image_occurrences"] == 1
    assert result.count("image.png") == 1
    parsed = _reference.pandoc_html(result, pandoc)
    assert next(parsed.iter("img")).get("alt") == "Legacy preview"


def test_each_image_of_a_vml_picture_is_shown(convert: Converter) -> None:
    # Pandoc reads only the first image of a VML picture, here one of two shapes.
    content = legacy_image()
    picture = content.find(f"./{W}r/{W}pict")
    assert picture is not None
    picture.append(copy.deepcopy(picture[0]))
    result, report, warnings = convert.reported(
        [content],
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
    )
    url = ".images/Synthetic%20Guide/image.png"
    assert result == f"![Legacy preview]({url})![Legacy preview]({url})\n"
    assert report.status("images") == "applied"
    assert "images:" not in warnings


def test_packaged_picture_fallback_is_used_for_unsupported_native_drawing(
    convert: Converter,
) -> None:
    content = ET.Element(W + "p")
    alternate = ET.SubElement(ET.SubElement(content, W + "r"), _ooxml.MC + "AlternateContent")
    choice = ET.SubElement(alternate, _ooxml.MC + "Choice", {"Requires": "wpg"})
    ET.SubElement(choice, W + "drawing")
    fallback = ET.SubElement(alternate, _ooxml.MC + "Fallback")
    picture = legacy_image().find(f"./{W}r/{W}pict")
    assert picture is not None
    fallback.append(picture)
    result, stats = convert(
        [content],
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
    )
    assert stats["image_occurrences"] == 1
    assert "Legacy preview" in result


def test_legacy_picture_with_a_text_box_keeps_each_once(convert: Converter) -> None:
    content = legacy_image()
    picture = content.find(f"./{W}r/{W}pict")
    assert picture is not None
    caption = ET.SubElement(
        picture, _ooxml.VML + "shape", {"id": "caption", "type": "#_x0000_t202"}
    )
    box = ET.SubElement(ET.SubElement(caption, _ooxml.VML + "textbox"), _ooxml.TEXT_BOX_CONTENT)
    box.append(paragraph("Caption text"))
    result, stats = convert(
        [content],
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
    )
    assert stats["image_occurrences"] == 1
    assert result.count("image.png") == 1
    assert result.count("Caption text") == 1


def test_emf_svg_publication_collision_gets_a_distinct_name(
    convert: Converter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def convert_vectors(
        drawings: dict[str, tuple[bytes, str]], _work: Path
    ) -> dict[str, _media.VectorResult]:
        assert [suffix for _data, suffix in drawings.values()] == [".emf"]
        return {name: (SVG, []) for name in drawings}

    monkeypatch.setattr(_media, "_convert_vectors", convert_vectors)
    result, report, warnings = convert.reported(
        [image("emf"), image("svg")],
        relations=(
            ("emf", "image", "media/image.emf", "Internal"),
            ("svg", "image", "media/image.svg", "Internal"),
        ),
        media={"image.emf": synthetic_emf(), "image.svg": SVG},
    )
    assert "](.images/Synthetic%20Guide/image.svg)" in result
    assert "](.images/Synthetic%20Guide/image-2.svg)" in result
    # A detail, which only --verbose shows.
    assert "Image name image.svg is already used; published as image-2.svg" in report.findings
    assert "Image name image.svg" not in warnings
    assert report.status("images") == "applied"
    assert report.status("checks") == "applied"


def test_media_with_same_basename_in_distinct_package_paths_are_not_flattened(
    convert: Converter,
) -> None:
    result, _stats = convert(
        [image("one"), image("two")],
        relations=(
            ("one", "image", "media/one/image.png", "Internal"),
            ("two", "image", "media/two/image.png", "Internal"),
        ),
        media={"one/image.png": PNG, "two/image.png": PNG},
    )
    assert "/one/image.png" in result
    assert "/two/image.png" in result


def test_missing_media_is_dropped_and_reported(convert: Converter) -> None:
    result, report, warnings = convert.reported(
        [paragraph("Before"), image(), paragraph("After")],
        relations=(("image", "image", "media/missing.png", "Internal"),),
    )
    assert "Before" in result
    assert "After" in result
    assert "![" not in result
    assert "Missing Word media part 'word/media/missing.png'; image dropped" in warnings
    assert report.status("images") == "not applied"
    assert dict(report.records["images"].skipped) == {
        "Missing Word media part '...'; image dropped": 1
    }


def test_external_images_are_not_fetched_and_are_reported(convert: Converter) -> None:
    result, report, warnings = convert.reported(
        [paragraph("Before"), image(), paragraph("After")],
        relations=(("image", "image", "https://example.test/image.png", "External"),),
    )
    assert "After" in result
    assert "![" not in result
    assert "example.test" not in result
    assert "not fetched: 'https://example.test/image.png'; image dropped" in warnings
    assert report.status("images") == "not applied"


def test_linked_image_with_an_embedded_copy_uses_the_copy(convert: Converter) -> None:
    content = image()
    blip = next(content.iter(_ooxml.DRAWING + "blip"))
    blip.set(R + "link", "linked")
    result, report, _warnings = convert.reported(
        [content],
        relations=(
            ("image", "image", "media/image.png", "Internal"),
            ("linked", "image", "cid:image001.png@01DA1234.5678ABCD", "External"),
        ),
        media={"image.png": PNG},
    )
    assert "](.images/Synthetic%20Guide/image.png)" in result
    assert "cid:" not in result
    assert report.status("images") == "applied"
    assert report.status("checks") == "applied"


def test_text_boxes_are_reported_without_stopping_conversion(convert: Converter) -> None:
    result, report, warnings = convert.reported(
        [
            paragraph("Before"),
            text_box("Boxed drawing text"),
            text_box("Boxed legacy text", legacy=True),
            paragraph("After"),
        ]
    )
    assert result == "Before\n\nBoxed drawing text\n\nBoxed legacy text\n\nAfter\n"
    assert dict(report.records["images"].skipped) == {
        "DrawingML text box: no embedded image; its text is kept after its paragraph": 1,
        "VML text box: no embedded image; its text is kept after its paragraph": 1,
    }
    assert "DrawingML text box at paragraph 2: no embedded image" in warnings


def test_unconverted_vector_image_is_published_as_is(
    convert: Converter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(drawings: dict[str, tuple[bytes, str]], _work: Path) -> dict[str, _media.VectorResult]:
        return {
            name: (f"synthetic {suffix} converter failure", [])
            for name, (_data, suffix) in drawings.items()
        }

    monkeypatch.setattr(_media, "_convert_vectors", fail)
    result, report, warnings = convert.reported(
        [image("emf")],
        relations=(("emf", "image", "media/diagram.emf", "Internal"),),
        media={"diagram.emf": synthetic_emf()},
    )
    assert "](.images/Synthetic%20Guide/diagram.emf)" in result
    assert report.status("images") == "applied"
    assert dict(report.records["image-conversion"].skipped) == {
        "EMF to SVG conversion failed; published as EMF": 1
    }
    assert "diagram.emf: synthetic .emf converter failure" in warnings


def test_wmf_drawings_are_converted_like_emf(
    convert: Converter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def convert_wmf(
        drawings: dict[str, tuple[bytes, str]], _work: Path
    ) -> dict[str, _media.VectorResult]:
        assert [suffix for _data, suffix in drawings.values()] == [".wmf"]
        return {name: (SVG, []) for name in drawings}

    monkeypatch.setattr(_media, "_convert_vectors", convert_wmf)
    result, report, _warnings = convert.reported(
        [image("wmf")],
        relations=(("wmf", "image", "media/diagram.wmf", "Internal"),),
        media={"diagram.wmf": b"synthetic WMF"},
    )
    assert "](.images/Synthetic%20Guide/diagram.svg)" in result
    assert dict(report.records["image-conversion"].applied) == {"WMF to SVG": 1}
    assert report.status("images") == "applied"


def test_a_documents_drawings_convert_together_and_report_separately(
    convert: Converter, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def convert_vectors(
        drawings: dict[str, tuple[bytes, str]], _work: Path
    ) -> dict[str, _media.VectorResult]:
        calls.append(sorted(suffix for _data, suffix in drawings.values()))
        return {
            name: (SVG, [f"{_media._WHOLE_PAGE}: synthetic reason"] if suffix == ".wmf" else [])
            for name, (_data, suffix) in drawings.items()
        }

    names = ("first", "second", "third")
    monkeypatch.setattr(_media, "_convert_vectors", convert_vectors)
    result, report, warnings = convert.reported(
        [image(name) for name in names],
        relations=(
            ("first", "image", "media/first.emf", "Internal"),
            ("second", "image", "media/second.emf", "Internal"),
            ("third", "image", "media/third.wmf", "Internal"),
        ),
        media={
            "first.emf": synthetic_emf(),
            "second.emf": synthetic_emf(1),
            "third.wmf": b"synthetic WMF",
        },
    )
    assert calls == [[".emf", ".emf", ".wmf"]]
    for name in names:
        assert f"](.images/Synthetic%20Guide/{name}.svg)" in result
    assert dict(report.records["image-conversion"].applied) == {
        "EMF to SVG": 2,
        "WMF to SVG": 1,
    }
    assert f"third.wmf: {_media._WHOLE_PAGE}: synthetic reason" in warnings
    assert warnings.count("synthetic reason") == 1


def test_libreoffice_converts_the_drawings_of_the_body_while_pandoc_reads_it(
    convert: Converter, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    calls: list[list[str]] = []

    def convert_vectors(
        drawings: dict[str, tuple[bytes, str]], _work: Path
    ) -> dict[str, _media.VectorResult]:
        calls.append(sorted(suffix for _data, suffix in drawings.values()))
        started.set()
        return {name: (SVG, []) for name in drawings}

    read_docx = _tree.read_docx

    def read_once_libreoffice_started(path: Path, media: Path, pandoc: str) -> _tree.Element:
        assert started.wait(timeout=10), "LibreOffice had not started when Pandoc read the docx"
        return read_docx(path, media, pandoc)

    monkeypatch.setattr(_media, "_convert_vectors", convert_vectors)
    monkeypatch.setattr(_tree, "read_docx", read_once_libreoffice_started)
    result, report, _warnings = convert.reported(
        [image("shown")],
        relations=(("shown", "image", "media/shown.emf", "Internal"),),
        media={"shown.emf": synthetic_emf()},
        # A drawing that the body does not show, such as a header's, is never converted.
        extras={"word/media/logo.emf": synthetic_emf(1)},
    )
    assert calls == [[".emf"]]
    assert "](.images/Synthetic%20Guide/shown.svg)" in result
    assert dict(report.records["image-conversion"].applied) == {"EMF to SVG": 1}


def test_a_failed_conversion_waits_for_libreoffice_before_removing_its_work(
    convert: Converter, monkeypatch: pytest.MonkeyPatch
) -> None:
    failing, finished = threading.Event(), threading.Event()

    def convert_vectors(
        drawings: dict[str, tuple[bytes, str]], _work: Path
    ) -> dict[str, _media.VectorResult]:
        # Still converting when the conversion fails.
        assert failing.wait(timeout=10)
        time.sleep(0.3)
        finished.set()
        return {name: (SVG, []) for name in drawings}

    def fail(_path: Path, _media: Path, _pandoc: str) -> _tree.Element:
        failing.set()
        raise _models.ConversionError("synthetic Pandoc failure")

    monkeypatch.setattr(_media, "_convert_vectors", convert_vectors)
    monkeypatch.setattr(_tree, "read_docx", fail)
    with pytest.raises(_models.ConversionError, match="synthetic Pandoc failure"):
        convert(
            [image("shown")],
            relations=(("shown", "image", "media/shown.emf", "Internal"),),
            media={"shown.emf": synthetic_emf()},
        )
    assert finished.is_set()


def test_picture_in_a_drawing_group_is_published(convert: Converter) -> None:
    result, report, _warnings = convert.reported(
        [paragraph("Before"), grouped_image(), paragraph("After")],
        relations=(("image", "image", "media/grouped.png", "Internal"),),
        media={"grouped.png": PNG},
    )
    assert ".images/Synthetic%20Guide/grouped.png" in result
    assert "Grouped diagram" in result
    assert "After" in result
    assert report.status("images") == "applied"
    assert report.status("checks") == "applied"


def test_dry_run_has_no_final_writes_and_retains_only_requested_bundle(
    source: Path, tmp_path: Path
) -> None:
    write_docx(source, [paragraph("Title.", "Heading1")])
    output = tmp_path / "new" / "document.md"
    intermediates = tmp_path / "intermediates"
    _pipeline.convert_docx(source, output, dry_run=True, keep_intermediates=intermediates)
    assert not output.parent.exists()
    assert (intermediates / "prepared.docx").is_file()
    assert (intermediates / "final.md").is_file()


def test_dry_run_without_diagnostics_leaves_directory_listing_unchanged(
    source: Path, tmp_path: Path
) -> None:
    write_docx(source, [paragraph("Title.", "Heading1")])
    before = set(tmp_path.iterdir())
    _pipeline.convert_docx(source, dry_run=True)
    assert set(tmp_path.iterdir()) == before


def test_tmpdir_inside_images_is_not_used_as_scratch(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_docx(source, [paragraph("Text")])
    images = tmp_path / ".images" / source.stem
    images.mkdir(parents=True)
    original = _pipeline._convert_in_work

    def inspect_work(
        paths: _models.ConversionPaths,
        work: Path,
        *,
        qualify_duplicates: bool,
        dry_run: bool,
        report: _diagnostics.TransformReport,
        math: Literal["github", "dollars"],
    ) -> dict[str, int]:
        assert not work.is_relative_to(images)
        return original(
            paths,
            work,
            qualify_duplicates=qualify_duplicates,
            dry_run=dry_run,
            report=report,
            math=math,
        )

    monkeypatch.setattr(tempfile, "tempdir", str(images))
    monkeypatch.setattr(_pipeline, "_convert_in_work", inspect_work)
    _pipeline.convert_docx(source, dry_run=True)
    assert not any(images.iterdir())
    assert not source.with_suffix(".md").exists()


def test_dry_run_with_replacement_permission_preserves_existing_batch(
    source: Path, tmp_path: Path
) -> None:
    write_docx(source, [paragraph("New", "Heading1")])
    output = source.with_suffix(".md")
    output.write_text("old markdown", encoding="utf-8")
    images = tmp_path / ".images" / "Synthetic Guide"
    images.mkdir(parents=True)
    (images / "old.png").write_bytes(PNG)
    _pipeline.convert_docx(source, dry_run=True, replace_generated=True)
    assert output.read_text(encoding="utf-8") == "old markdown"
    assert (images / "old.png").read_bytes() == PNG


def test_explicit_markdown_replacement_is_normal_without_directory_content(
    convert: Converter, tmp_path: Path
) -> None:
    output = tmp_path / "selected.md"
    output.write_text("old", encoding="utf-8")
    result, _stats = convert([paragraph("new")], output=output)
    assert result == "new\n"


def test_unsafe_destination_is_rejected_before_conversion_or_intermediates_creation(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_docx(source, [paragraph("Text")])
    output = tmp_path / "alias.md"
    os.link(source, output)
    intermediates = tmp_path / "intermediates"
    prepare = mock.Mock()
    monkeypatch.setattr(_prepare, "prepare_document", prepare)
    with pytest.raises(_models.ConversionError, match=r"aliases the source DOCX"):
        _pipeline.convert_docx(source, output, keep_intermediates=intermediates)
    prepare.assert_not_called()
    assert not intermediates.exists()


def test_word_conversion_needs_pandoc(source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_docx(source, [paragraph("Title", "Heading1"), paragraph("Text")])
    hide_pandoc(monkeypatch)
    with pytest.raises(_models.ConversionError, match=r"needs Pandoc, which is not installed"):
        _pipeline.convert_docx(source)
    assert not source.with_suffix(".md").exists()


def test_hidden_text_is_dropped(convert: Converter) -> None:
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
    result, report, _warnings = convert.reported(body)
    assert "Shown text." in result
    assert "SECRET" not in result
    assert "HIDDEN" not in result
    assert "  hidden-text: applied (2)" in report.summary()
    assert report.status("checks") == "applied"


def test_unicode_spaces_become_spaces_except_an_indent(convert: Converter) -> None:
    body = [
        paragraph("Title", "Heading1"),
        paragraph("Clock 100\u00a0MHz and\u2002bit-bash\u200b."),
        paragraph("if (a) {"),
        paragraph("\u00a0 \u00a0 \u00a0 b = 1;\u00a0 // set"),
        paragraph("}"),
        table(row(cell("Name"), cell("Value"), header=True), row(cell("a\u202fb"), cell("\u00a0"))),
        # Word alt text, as Office writes it, can hold a zero-width space.
        image(alt="\u200bDiagram\u00a0one"),
    ]
    relations = (("image", "image", "media/image.png", "Internal"),)
    media = {"image.png": PNG}
    result, report, warnings = convert.reported(body, relations=relations, media=media)
    assert "Clock 100 MHz and bit-bash." in result
    # Markdown keeps only no-break spaces at the start of a paragraph.
    assert "if (a) {\n\n\xa0\xa0\xa0\xa0\xa0\xa0b = 1; // set\n\n}" in result
    assert "| a b | |" in result
    assert "![Diagram one](" in result
    assert result.count("\xa0") == 6
    assert "  spaces: applied (11)" in report.summary()
    assert report.status("checks") == "applied", warnings


def test_symbol_font_arrows_become_arrows_that_every_font_has(convert: Converter) -> None:
    # Pandoc reads Wingdings' arrows as Supplemental Arrows-C characters, which few fonts have.
    def arrow(before: str, symbol: str, after: str) -> ET.Element:
        word = _ooxml.word_run
        return paragraph_of(word(before), font_run(symbol, "Wingdings"), word(after))

    result, report, _warnings = convert.reported(
        [
            arrow("Request ", "\u00e0", " response"),
            arrow("Address ", "\u00df", " counter"),
            table(
                row(cell("Path"), header=True), row(cell(arrow("a ", "\u00e0", " b"))), columns=1
            ),
        ]
    )
    assert "Request → response" in result
    assert "Address ← counter" in result
    assert "| a → b |" in result
    assert "  symbols: applied (3)" in report.summary()


def test_header_breaks_become_spaces_and_cells_lose_empty_edge_lines(convert: Converter) -> None:
    def broken(first: str, second: str) -> ET.Element:
        run = _ooxml.word_run(first)
        ET.SubElement(run, W + "br")
        ET.SubElement(run, W + "t").text = second
        return run

    result, report, warnings = convert.reported(
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
    assert (
        "| Addr [31:24] | Two paras |\n| --- | --- |\n| line1<br>line2 | in<br><br>side |\n"
        "| trailing | leading |\n| end | x |\n" in result
    )
    assert report.status("checks") == "applied", warnings


def test_monospace_paragraphs_become_a_fenced_code_block(convert: Converter) -> None:
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
    result, report, warnings = convert.reported(body)
    assert (
        'Before.\n\n````text\nif (state == "busy") {\n    wait();\n\n'
        "} # end - done\n```\n````\n\nAfter." in result
    )
    assert (
        "  code: applied (1 code block(s), 3 curly quote(s) and dash(es) straightened)"
        in report.summary()
    )
    assert report.status("checks") == "applied", warnings


def test_a_one_cell_monospace_table_becomes_a_code_block(convert: Converter) -> None:
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
    result, report, warnings = convert.reported(body)
    assert "Before.\n\n```text\nx = <a> & `b`\n\n  y = 2\n```\n\nAfter." in result
    assert "| `not code` | Next |" in result
    assert (
        "  code: applied (1 code block(s), 1 inline code span(s), "
        "1 one-cell table(s) made code blocks)" in report.summary()
    )
    assert report.status("checks") == "applied", warnings


def test_monospace_text_becomes_inline_code_outside_headings_links_and_fields(
    convert: Converter,
) -> None:
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
    result, report, warnings = convert.reported(
        body, relations=(("web", "hyperlink", "https://example.test/doc", "External"),)
    )
    assert "Set `ctrl_reg[3:0]` to “idle”." in result
    assert "Run `` `cmd` `` now." in result
    assert "Broken `one`\\\n`two` end." in result
    assert "[linked label](https://example.test/doc)" in result
    assert "By Field Author" in result
    assert "## Monospace heading" in result
    assert "| mode | `a\\|b` |" in result
    assert "  code: applied (5 inline code span(s))" in report.summary()
    assert report.status("checks") == "applied", warnings


def test_a_failing_layer_is_reported_and_the_rest_is_published(
    convert: Converter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_markdown: str) -> tuple[str, int]:
        raise RuntimeError("synthetic layer failure")

    monkeypatch.setattr(_normalization, "trim_trailing_whitespace", fail)
    result, report, _warnings = convert.reported([paragraph("Text")])
    assert result == "Text\n"
    assert report.status("cleanup") == "not applied"
    assert report.records["cleanup"].failure == "RuntimeError: synthetic layer failure"
    assert report.status("checks") == "applied"


def test_heading_level_seven_is_kept_as_a_paragraph(convert: Converter) -> None:
    deep = paragraph("Level seven")
    properties = ET.Element(W + "pPr")
    ET.SubElement(properties, W + "outlineLvl", {W + "val": "6"})
    deep.insert(0, properties)
    result, report, _warnings = convert.reported([paragraph("Title", "Heading1"), deep])
    assert "Level seven" in result
    assert "# Level seven" not in result
    assert dict(report.records["headings"].skipped) == {
        "Word heading levels 7-9 have no Markdown equivalent; kept as a paragraph": 1
    }


def test_unnamed_and_duplicate_bookmarks_are_reported(convert: Converter) -> None:
    result, report, _warnings = convert.reported(
        [
            bookmark("", "Unnamed target"),
            bookmark("_Twice", "First", "Heading1"),
            bookmark("_Twice", "Second", "Heading1"),
            hyperlink("Go", anchor="_Twice"),
        ]
    )
    assert "[Go](#first)" in result
    assert dict(report.records["bookmarks"].skipped) == {
        "Word bookmark has no name; dropped": 1,
        "duplicate Word bookmark name; the later one is dropped": 1,
    }


# Headings 1, 3, and 6 keep their levels; links to their bookmarks lead to them.
def test_skipped_heading_levels_stay_and_bookmark_links_follow(
    source: Path, tmp_path: Path
) -> None:
    body = [
        paragraph("Preamble"),
        bookmark("_A", "First.", "Heading1"),
        hyperlink("Deeper", anchor="_B"),
        bookmark("_B", "Third.", "Heading3"),
        paragraph("Detail.", "Heading6"),
        hyperlink("Up", anchor="_A"),
    ]
    write_docx(source, body)
    _pipeline.convert_docx(source, tmp_path / "guide.md")
    assert (tmp_path / "guide.md").read_text(encoding="utf-8") == (
        "Preamble\n\n# First.\n\n[Deeper](#third)\n\n### Third.\n\n###### Detail.\n\n[Up](#first)\n"
    )


def test_duplicate_qualification_is_opt_in(convert: Converter) -> None:
    result, stats = convert(
        [
            bookmark("_A", "Same.", "Heading3"),
            bookmark("_B", "Same.", "Heading3"),
            hyperlink("First", anchor="_A"),
            hyperlink("Second", anchor="_B"),
        ],
        qualify=True,
    )
    assert "### Same. - occurrence 1" in result
    assert "### Same. - occurrence 2" in result
    assert "<a " not in result
    assert "[First](#same---occurrence-1)" in result
    assert "[Second](#same---occurrence-2)" in result
    assert stats["qualified_headings"] == 2


def test_generated_toc_results_are_retained(convert: Converter) -> None:
    index = ET.Element(W + "p")
    field = ET.SubElement(index, W + "fldSimple", {W + "instr": 'TOC \\o "1-3" \\h'})
    field.append(_ooxml.word_run("Cached contents entry"))
    result, _stats = convert(
        [
            paragraph("Contents", "Heading1"),
            index,
            paragraph("Body", "Heading1"),
        ]
    )
    assert "# Contents\n\nCached contents entry\n" in result


def test_complex_toc_field_results_and_blank_rows_survive(convert: Converter, pandoc: str) -> None:
    start = ET.Element(W + "p")
    run = ET.SubElement(start, W + "r")
    ET.SubElement(run, W + "fldChar", {W + "fldCharType": "begin"})
    code_run = ET.SubElement(start, W + "r")
    ET.SubElement(code_run, W + "instrText").text = ' TOC \\o "1-3" \\h '
    end_run = ET.SubElement(start, W + "r")
    ET.SubElement(end_run, W + "fldChar", {W + "fldCharType": "separate"})
    end = ET.Element(W + "p")
    ET.SubElement(ET.SubElement(end, W + "r"), W + "fldChar", {W + "fldCharType": "end"})
    result, _stats = convert(
        [
            paragraph("Contents", "Heading1"),
            start,
            paragraph("Cached field entry"),
            end,
            table(row(cell(""), cell(""))),
        ]
    )
    assert "Cached field entry" in result
    assert len(list(_reference.pandoc_html(result, pandoc).iter("tr"))) == 1


@pytest.mark.parametrize(
    "simple", [pytest.param(False, id="complex-fields"), pytest.param(True, id="simple-fields")]
)
def test_toc_page_reference_fields_are_dropped_from_their_links(
    simple: bool, convert: Converter, pandoc: str
) -> None:
    entries: list[ET.Element] = []
    for name, title, page in (("_One", "First ", "1"), ("_Two", "Second ", "2")):
        entry = hyperlink(title, anchor=name)
        link = entry.find(W + "hyperlink")
        assert link is not None
        link.extend(page_reference(name, page, simple=simple))
        entries.append(entry)
    result, report, _warnings = convert.reported(
        [
            paragraph("Contents.", "Heading1"),
            *entries,
            bookmark("_One", "First.", "Heading1"),
            bookmark("_Two", "Second.", "Heading3"),
        ]
    )

    parsed = _reference.pandoc_html(result, pandoc)
    assert [(node.get("href"), "".join(node.itertext())) for node in parsed.iter("a")] == [
        ("#first", "First"),
        ("#second", "Second"),
    ]
    assert report.records["fields"].applied["table-of-contents page number(s) dropped"] == 2
    assert "## Second.\n" in result


def test_word_table_of_contents_keeps_entry_numbers_and_links_without_pages(
    convert: Converter,
) -> None:
    result, report, _warnings = convert.reported(
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

    assert "[1 Introduction](#introduction)\n\n[2 Setup](#setup)\n" in result
    assert report.status("links") == "applied"
    assert report.status("checks") == "applied"
    assert not report.records["checks"].skipped


def test_literal_word_placeholders_entities_and_tables_keep_their_text(
    convert: Converter, pandoc: str
) -> None:
    split = paragraph("<sp", bold=True)
    split.append(_ooxml.word_run("eeds>"))
    result, _stats = convert(
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

    parsed = _reference.pandoc_html(result, pandoc)
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
        assert literal in visible
    assert not list(parsed.iter("speeds"))
    assert not list(parsed.iter("blockquote"))
    assert _validation.collect_references(parsed).links == ["#mode-copy"]
    assert "".join(next(parsed.iter("h3")).itertext()) == "<Mode> &copy;."


def test_urls_as_link_text_stay_one_link(convert: Converter, pandoc: str) -> None:
    # GFM's bare-URL autolinks would nest a link in the link text and break the outer one.
    url = "https://example.test/a_b_c?x=1&y=2"
    result, report, _warnings = convert.reported(
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
    assert "[[" not in result
    # Link text and autolinks keep underscores without protected backslashes.
    assert _reference.BACKSLASH_REFERENCE not in result
    links = [
        (node.get("href"), "".join(node.itertext()))
        for node in _reference.pandoc_html(result, pandoc).iter("a")
    ]
    assert links == [
        (url, url),
        ("https://www.example.test/a_b", "www.example.test/a_b"),
        # A bare URL in prose becomes an autolink of its own.
        ("https://example.test/plain_path", "https://example.test/plain_path"),
    ]
    assert "See <https://example.test/plain_path> for details." in result
    assert report.status("checks") == "applied"
    assert not report.records["checks"].skipped


def test_hyperlink_field_preserves_destination_label_and_screen_tip(convert: Converter) -> None:
    content = ET.Element(W + "p")
    field = ET.SubElement(
        content,
        W + "fldSimple",
        {
            W + "instr": ' HYPERLINK "https://example.test/item" \\o "Say \\"hi\\"" ',
        },
    )
    field.append(_ooxml.word_run("Source label"))
    result, _stats = convert([content])
    assert result == '[Source label](https://example.test/item "Say \\"hi\\"")\n'


def test_complex_hyperlink_field_and_tooltip_preserve_link(convert: Converter) -> None:
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
    result, _stats = convert([content])
    assert result == '[Cached label](https://example.test/a "Tip")\n'


@pytest.mark.parametrize(
    "bold", [pytest.param(False, id="plain"), pytest.param(True, id="bold-tab-and-images")]
)
def test_field_boundary_run_content_survives_real_conversion(
    bold: bool, convert: Converter, pandoc: str
) -> None:
    content = boundary_hyperlink_field()
    if bold:
        for run in (content[0], content[-1]):
            properties = ET.Element(W + "rPr")
            ET.SubElement(properties, W + "b")
            run.insert(0, properties)
        content[0].insert(2, ET.Element(W + "tab"))
        for run in (content[0], content[-1]):
            drawing = image().find(f"./{W}r/{W}drawing")
            assert drawing is not None
            run.insert(2, drawing)
    result, _stats = convert(
        [content],
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
    )
    parsed = _reference.pandoc_html(result, pandoc)
    visible = "".join(parsed.itertext())
    assert "Before" in visible
    assert "Cached label" in visible
    assert "After" in visible
    assert _validation.collect_references(parsed).links == ["https://example.test/plain"]
    if bold:
        strong = ["".join(node.itertext()) for node in parsed.iter("strong")]
        assert any("Before" in text for text in strong)
        assert any("After" in text for text in strong)
        assert len(_validation.collect_references(parsed).images) == 2


@pytest.mark.parametrize("kind", ["complex", "simple"])
def test_hyperlink_url_containing_toc_is_not_a_toc_field(
    kind: str, convert: Converter, pandoc: str
) -> None:
    target = "https://example.test/TOC"
    instruction = f' HYPERLINK "{target}" '
    if kind == "complex":
        content = boundary_hyperlink_field()
        code = content.find(f".//{W}instrText")
        assert code is not None
        code.text = instruction
    else:
        content = ET.Element(W + "p")
        field = ET.SubElement(content, W + "fldSimple", {W + "instr": instruction})
        field.append(_ooxml.word_run("Cached label"))
    result, _stats = convert([content])
    assert _validation.collect_references(_reference.pandoc_html(result, pandoc)).links == [target]


def test_custom_style_outline_level_is_preserved(convert: Converter) -> None:
    source = paragraph("Custom heading.")
    properties = ET.Element(W + "pPr")
    ET.SubElement(properties, W + "outlineLvl", {W + "val": "4"})
    source.insert(0, properties)
    result, _stats = convert([source])
    assert "##### Custom heading.\n" in result


def test_an_encrypted_or_word_97_document_is_refused_before_conversion(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both are OLE compound files rather than ZIP packages.
    source.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"EncryptedPackage")
    run = mock.Mock()
    monkeypatch.setattr(_reference, "run_command", run)
    with pytest.raises(
        _models.PackageError,
        match=r"it is encrypted, or in the Word 97-2003 format; "
        r"convert an unencrypted \.docx copy$",
    ):
        _pipeline.convert_docx(source)
    run.assert_not_called()


def test_a_damaged_package_is_refused_with_the_reason(source: Path) -> None:
    source.write_bytes(b"PK not a zip")
    with pytest.raises(
        _models.PackageError, match=r"^Cannot read DOCX package .+: File is not a zip file$"
    ):
        _pipeline.convert_docx(source)


def test_the_main_document_part_is_found_through_the_package_relationships(
    source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Word names it word/document.xml, but generators may not; this package also targets its
    # parts by absolute names, as the Open XML SDK can.
    write_docx(
        source,
        [paragraph("Overview", "Heading1"), image()],
        relations=(
            ("styles", "styles", "/word/styles.xml", "Internal"),
            ("image", "image", "/word/media/image.png", "Internal"),
        ),
        media={"image.png": PNG},
        document="word/document2.xml",
    )
    stats = _pipeline.convert_docx(source)
    assert _publication.requested_output(source, None).read_text(encoding="utf-8") == (
        "# Overview\n\n![Diagram](.images/Synthetic%20Guide/image.png)\n"
    )
    assert stats["image_occurrences"] == 1
    assert "warning" not in capsys.readouterr().err


def test_a_package_that_expands_too_far_is_rejected_before_it_is_read(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_docx(source, [paragraph("Text")])
    expanded = sum(entry.file_size for entry in zipfile.ZipFile(source).infolist())
    monkeypatch.setattr(packages, "MAX_EXPANDED_SIZE", expanded - 1)
    read = mock.Mock(side_effect=AssertionError("a package member was read"))
    monkeypatch.setattr(zipfile.ZipFile, "read", read)
    with pytest.raises(_models.PackageError, match=r"^DOCX package expands to .* MiB, more than"):
        _pipeline.convert_docx(source)
    read.assert_not_called()
    assert not source.with_suffix(".md").exists()


def test_word_editing_protection_and_classification_metadata_remain_in_bundle(
    source: Path, tmp_path: Path
) -> None:
    protection = (
        b'<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b'<w:documentProtection w:edit="readOnly" w:enforcement="1"/></w:settings>'
    )
    write_docx(source, [paragraph("Protected source")], extras={"word/settings.xml": protection})
    diagnostics = tmp_path / "diagnostics"
    _pipeline.convert_docx(source, keep_intermediates=diagnostics)
    with zipfile.ZipFile(diagnostics / "prepared.docx") as archive:
        assert archive.read("word/settings.xml") == protection


def test_one_level_lists_in_table_cells_become_lines(convert: Converter) -> None:
    result, report, warnings = convert.reported(
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
    assert "| A | 1. first<br>2. second |" in result
    assert "| B | • bullet |" in result
    assert "| C | 1. typed<br>3. listed |" in result
    assert "<table" not in result
    assert "remains HTML" not in warnings
    # The final-table check expects the markers before Word's list items only.
    assert report.status("checks") == "applied"


def test_indented_and_unnumbered_cell_paragraphs_become_lines(convert: Converter) -> None:
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
    result, report, warnings = convert.reported(
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
    assert "| A | Lead<br>Indented note |" in result
    assert "| B | 1. first<br>more about first<br>2. second |" in result
    assert "<table" not in result
    assert "remains HTML" not in warnings
    assert report.status("checks") == "applied"


def test_sub_sup_mark_and_u_keep_tables_and_links_markdown(convert: Converter) -> None:
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
    ET.SubElement(link, W + "hyperlink", {R + "id": "site"}).append(run("Spec.pptx", "u", "single"))
    result, report, warnings = convert.reported(
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
    assert r"| x<sup>2</sup> | H<sub>2</sub>O, <mark>new</mark> and <u>a\|b \*c\*</u> |" in result
    # Links show as underlined, so their text drops Word's underline.
    assert "[Spec.pptx](https://example.test/Spec.pptx)" in result
    assert "<table" not in result
    assert "<a href" not in result
    assert "remains HTML" not in warnings
    assert report.status("checks") == "applied"


def test_brackets_stay_escaped_only_where_they_could_be_read_differently(
    convert: Converter,
) -> None:
    result, report, _warnings = convert.reported(
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
    assert r"Bits [3:0] of ctrl[7]; the text \[a\](b) is not a link." in result
    assert r"1. \[1:0\]: low bits" in result
    assert "| mode[1:0] | 7) seven |" in result
    assert report.records["cleanup"].applied["unneeded escape(s) removed"] == 7


def test_list_items_without_text_are_omitted_keeping_word_numbers(convert: Converter) -> None:
    result, report, _warnings = convert.reported(
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
    assert re.search(r"(?m)^2\. a$", result)
    assert re.search(r"(?m)^3\.$", result)
    assert re.search(r"(?m)^4\. b$", result)
    assert not re.search(r"(?m)^(1|5)\.", result)
    assert re.search(r"(?m)^- x$", result)
    assert re.search(r"(?m)^- y$", result)
    assert not re.search(r"(?m)^-$", result)
    assert report.records["lists"].applied["empty Word list item(s) omitted"] == 3


def test_deleted_list_items_and_tables_are_not_written(convert: Converter) -> None:
    # Track Changes deletions: a list item's text and paragraph mark, and a table's rows.
    item = numbered("")
    run, properties = item.find(W + "r"), item.find(W + "pPr")
    assert run is not None
    assert properties is not None
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
    result, _stats = convert(
        [
            paragraph("Intro"),
            table(*rows, columns=1),
            numbered("one"),
            item,
            numbered("two"),
        ],
        extras={"word/numbering.xml": numbering_xml("decimal", 1)},
    )
    assert "<table" not in result
    assert "Removed" not in result
    assert "1. one\n2. two" in result


def test_strict_open_xml_converts_as_the_transitional_form_does(
    convert: Converter, source: Path
) -> None:
    # Word can save in Strict Office Open XML, which names the same parts in other namespaces.
    body = [
        paragraph("Title", "Heading1"),
        numbered("Step"),
        hyperlink("Site", relation="site"),
        image(),
        table(row(cell("a"), cell("b")), row(cell("1"), cell("2"))),
    ]
    relations: Relations = (
        ("site", "hyperlink", "https://example.test/", "External"),
        ("image", "image", "media/image.png", "Internal"),
    )
    media = {"image.png": PNG}
    extras = {"word/numbering.xml": numbering_xml()}
    transitional, _stats = convert(body, relations=relations, media=media, extras=extras)
    write_docx(source, body, relations=relations, media=media, extras=extras)
    with zipfile.ZipFile(source) as archive:
        parts = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(
                name,
                re.sub(
                    rb"http://schemas\.openxmlformats\.org/"
                    rb"(wordprocessingml|drawingml|officeDocument)/2006/",
                    rb"http://purl.oclc.org/ooxml/\1/",
                    data,
                ),
            )
    with zipfile.ZipFile(source) as archive:
        assert b"http://purl.oclc.org/ooxml/wordprocessingml/main" in archive.read(
            "word/document.xml"
        )
    _pipeline.convert_docx(source, replace_generated=True)
    assert source.with_suffix(".md").read_text(encoding="utf-8") == transitional


def test_accepted_view_and_package_metadata_are_consistent(source: Path, tmp_path: Path) -> None:
    content = ET.Element(W + "p")
    insertion = ET.SubElement(content, W + "ins")
    insertion.append(_ooxml.word_run("Accepted text"))
    deletion = ET.SubElement(content, W + "del")
    deletion.append(_ooxml.word_run("Deleted text"))
    custom = b"<Properties><Category>Example</Category></Properties>"
    write_docx(source, [content], extras={"docProps/custom.xml": custom})
    original = source.read_bytes()
    diagnostics = tmp_path / "debug"
    _pipeline.convert_docx(source, keep_intermediates=diagnostics)
    result = source.with_suffix(".md").read_text(encoding="utf-8")
    assert "Accepted text" in result
    assert "Deleted text" not in result
    assert source.read_bytes() == original
    with zipfile.ZipFile(diagnostics / "prepared.docx") as archive:
        assert archive.read("docProps/custom.xml") == custom


def test_source_local_hyperlink_is_rebased_and_validated(
    convert: Converter, tmp_path: Path
) -> None:
    asset = tmp_path / "Other File.txt"
    asset.write_text("retained local target", encoding="utf-8")
    output = tmp_path / "different" / "guide.md"
    result, _stats = convert(
        [hyperlink("Local target", relation="local")],
        output=output,
        relations=(("local", "hyperlink", "Other%20File.txt", "External"),),
    )
    assert "../Other%20File.txt" in result


def test_prepared_markdown_replaces_stale_content_for_fragment_validation(
    source: Path, tmp_path: Path
) -> None:
    output = tmp_path / "guide.md"
    output.write_text("# Old content\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    write_docx(
        source,
        [
            bookmark("_Fresh", "New", "Heading1"),
            hyperlink("Fresh", relation="fresh"),
            hyperlink("Outside", relation="outside"),
        ],
        relations=(
            ("fresh", "hyperlink", "guide.md#new", "External"),
            ("outside", "hyperlink", "outside.md#outside", "External"),
        ),
    )

    report = _diagnostics.TransformReport()
    _pipeline.convert_docx(source, output, replace_generated=True, report=report)

    result = output.read_text(encoding="utf-8")
    assert "# New" in result
    assert "[Fresh](#new)" in result
    assert "_Fresh" not in result
    assert report.status("checks") == "applied"
    assert not report.records["checks"].skipped
    assert outside.read_text(encoding="utf-8") == "# Outside\n"


def test_links_to_other_markdown_files_are_kept(source: Path, tmp_path: Path) -> None:
    folder = tmp_path / "guide"
    folder.mkdir()
    other = folder / "other.md"
    other.write_text("# Other\n", encoding="utf-8")
    write_docx(
        source,
        [hyperlink("Other", relation="other")],
        relations=(("other", "hyperlink", "guide/other.md#other", "External"),),
    )

    _pipeline.convert_docx(source, tmp_path / "guide.md")

    assert other.read_text(encoding="utf-8") == "# Other\n"
    assert "guide/other.md#other" in (tmp_path / "guide.md").read_text(encoding="utf-8")


def test_replaced_image_tree_cannot_supply_stale_link_targets(
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "guide.md"
    output.write_text("# Old root\n", encoding="utf-8")
    images = tmp_path / ".images" / "guide"
    images.mkdir(parents=True)
    old_image = images / "old.svg"
    old_image.write_bytes(SVG)
    write_docx(
        source,
        [hyperlink("Stale asset", relation="old")],
        relations=(("old", "hyperlink", ".images/guide/old.svg", "External"),),
    )
    publish = mock.Mock(wraps=_publication.publish_outputs)
    monkeypatch.setattr(_publication, "publish_outputs", publish)

    report = _diagnostics.TransformReport()
    _pipeline.convert_docx(source, output, replace_generated=True, report=report)

    publish.assert_called_once()
    assert "a linked image file is missing" in report.records["checks"].skipped
    assert "Missing linked asset in guide.md: .images/guide/old.svg" in capsys.readouterr().err
    assert not old_image.exists()


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        pytest.param(
            "https://example.test/topic_(detail)",
            "https://example.test/topic_%28detail%29",
            id="parentheses",
        ),
        pytest.param(
            "https://example.test/R\u00e9sum\u00e9",
            "https://example.test/R%C3%A9sum%C3%A9",
            id="non-ascii",
        ),
        pytest.param(
            "https://example.test/a%2Fb?q=x%26y&percent=%2525",
            "https://example.test/a%2Fb?q=x%26y&percent=%2525",
            id="percent-escapes",
        ),
        pytest.param(
            "https://example.test/?q=one&copy;=two&notit=three",
            "https://example.test/?q=one&copy;=two&notit=three",
            id="entity-like-query",
        ),
        pytest.param(
            "https://example.test/?literal=&amp;value",
            "https://example.test/?literal=&amp;value",
            id="literal-entity",
        ),
        pytest.param(
            "https://[2001:db8::1]/topic_(detail)",
            "https://[2001:db8::1]/topic_%28detail%29",
            id="ipv6-host",
        ),
    ],
)
def test_external_url_escaping_survives_real_conversion_and_tables(
    target: str, expected: str, convert: Converter, pandoc: str
) -> None:
    result, _stats = convert(
        [
            hyperlink("Outside", relation="url"),
            table(
                row(cell("Link"), header=True),
                row(cell(hyperlink("Pipe", relation="url"))),
                columns=1,
            ),
            table(row(cell(hyperlink("HTML", relation="url"), colspan=2))),
        ],
        relations=(("url", "hyperlink", target, "External"),),
    )
    rendered = _validation.collect_references(_reference.pandoc_html(result, pandoc)).links
    # The same in a paragraph, a pipe table, and an HTML table.
    assert rendered == [expected] * 3


def test_pandoc_generated_docx_preserves_unicode_parenthesized_external_url(
    source: Path, tmp_path: Path, pandoc: str
) -> None:
    target = "https://example.test/R\u00e9sum\u00e9_(detail)"
    _reference.run_command(
        [pandoc, "--from=gfm+raw_html", "--to=docx", f"--output={source}"],
        "Synthetic Markdown-to-Word",
        input_text=f"# Unicode guide\n\n[External reference]({target})\n",
    )
    output = tmp_path / "generated.md"

    result = subprocess.run(
        [*COMMAND, str(source), "-o", str(output)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    links = _validation.collect_references(
        _reference.pandoc_html(output.read_text(encoding="utf-8"), pandoc)
    ).links
    assert links == ["https://example.test/R%C3%A9sum%C3%A9_%28detail%29"]


def test_broken_local_hyperlink_is_kept_and_reported(convert: Converter) -> None:
    result, report, warnings = convert.reported(
        [hyperlink("Missing", relation="local")],
        relations=(("local", "hyperlink", "missing.txt", "External"),),
    )
    assert "[Missing](missing.txt)" in result
    assert "checks: Broken retained local link in Synthetic Guide.md: missing.txt" in warnings
    summary = report.summary()
    assert "  checks: 1 issue(s)" in summary
    assert "    issue (1): a local link target does not exist" in summary


def test_malformed_uri_is_kept_and_reported(convert: Converter) -> None:
    result, _report, warnings = convert.reported(
        [hyperlink("Broken", relation="broken")],
        relations=(("broken", "hyperlink", "https://[bad", "External"),),
    )
    assert "Broken" in result
    assert "Invalid retained URI" in warnings
