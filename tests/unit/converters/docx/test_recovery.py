"""Restoring literal text that conversion protected: tokens, line breaks, and backslashes."""

from __future__ import annotations

import json
import re
from xml.etree import ElementTree as ET

import pytest

from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import recovery as _recovery
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.pandoc import JSONValue
from tests.support.docx import W


def test_literal_recovery_distinguishes_prose_code_and_generated_html(pandoc: str) -> None:
    source = _models.SourceDocument(
        ET.Element(W + "document"),
        "SOURCE",
        literal_tokens={"SOURCELT": "<", "SOURCEGT": ">", "SOURCEAMP": "&"},
    )
    encoded = "SOURCELTspeedsSOURCEGT SOURCEAMPcopy;"
    markdown = f"{encoded}\n\n`{encoded}`\n\n```text\n{encoded}\n```\n\n<div>{encoded}</div>\n"

    result = _recovery.restore_literal_text(markdown, source, pandoc)
    parsed = _reference.pandoc_html(result, pandoc)

    assert "SOURCE" not in result
    assert not list(parsed.iter("speeds"))
    assert "".join(parsed.itertext()).count("<speeds> &copy;") == 4
    assert ["".join(node.itertext()).strip() for node in parsed.iter("code")] == [
        "<speeds> &copy;",
        "<speeds> &copy;",
    ]
    assert list(parsed.iter("div"))


def test_literal_backticks_backslashes_and_hashes_stay_text(pandoc: str) -> None:
    source = _models.SourceDocument(
        ET.Element(W + "document"),
        "SOURCE",
        literal_tokens={"SOURCEBT": "`", "SOURCEBS": "\\", "SOURCEHASH": "#"},
    )
    markdown = (
        "Use SOURCEBTuvm\\_info and SOURCEBTuvm\\_error.\n\n"
        "SOURCEHASH of entries\n\n"
        "Path C:SOURCEBStools\\\\a and SOURCEBS\\*b, SOURCEBSSOURCEBSserver\n"
    )

    result = _recovery.restore_literal_text(markdown, source, pandoc)
    parsed = _reference.pandoc_html(result, pandoc)

    assert "SOURCE" not in result
    assert [" ".join("".join(node.itertext()).split()) for node in parsed.iter("p")] == [
        "Use `uvm_info and `uvm_error.",
        "# of entries",
        "Path C:\\tools\\a and \\*b, \\\\server",
    ]
    assert not list(parsed.iter("code"))
    assert not list(parsed.iter("h1"))


def test_word_line_breaks_become_paragraph_breaks_inside_blocks(pandoc: str) -> None:
    source = _models.SourceDocument(
        ET.Element(W + "document"),
        "SOURCE",
        literal_tokens={"SOURCELITERALBREAK": " "},
    )
    markdown = (
        "first lineSOURCELITERALBREAKsecond line\n\n"
        "# headSOURCELITERALBREAKtwo\n\n"
        "SOURCELITERALBREAKedgeSOURCELITERALBREAK\n\n"
        "| aSOURCELITERALBREAKb | c |\n| --- | --- |\n"
    )

    result = _recovery.restore_literal_text(markdown, source, pandoc)

    assert "first line\n\nsecond line\n" in result
    assert "# head two\n" in result
    assert "\nedge\n" in result
    assert re.search(r"\| a b +\| c +\|", result)
    assert "SOURCE" not in result
    assert (source.restored_breaks, source.heading_breaks) == (1, 1)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("a\\*b", id="asterisk"),
        pytest.param("c\\_d and e_ f", id="underscores"),
        pytest.param("\\\\server\\share", id="unc-path"),
        pytest.param("end\\", id="trailing-backslash"),
        pytest.param("x\\`y` z", id="backticks"),
        pytest.param("C:\\tools", id="before-a-letter"),
    ],
)
def test_backslashes_before_punctuation_survive_the_commonmark_writer(
    text: str, pandoc: str
) -> None:
    document: JSONValue = {
        "pandoc-api-version": [1, 23, 1],
        "meta": {},
        "blocks": [{"t": "Para", "c": [{"t": "Str", "c": text}]}],
    }
    written = _reference.run_command(
        [pandoc, "--from=json", "--to=gfm+raw_html", "--wrap=none"],
        "Backslash test",
        input_text=json.dumps(_reference.protect_backslashes(document)),
    )
    parsed = _reference.pandoc_html(written, pandoc)
    assert "".join(parsed.itertext()).strip() == text
    # Only a backslash before punctuation needs protecting.
    protected = _reference.BACKSLASH_REFERENCE in written
    assert protected == (text != "C:\\tools")
