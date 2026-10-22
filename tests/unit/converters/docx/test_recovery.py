"""Synthetic DOCX regression coverage."""

from __future__ import annotations

import json
import re
import unittest
from xml.etree import ElementTree as ET

from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import recovery as _recovery
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.pandoc import JSONValue
from tests.support.docx import (
    W,
)
from tests.support.suites import pinned_pandoc


class LiteralTextTests(unittest.TestCase):
    def test_literal_recovery_distinguishes_prose_code_and_generated_html(self) -> None:
        source = _models.SourceDocument(
            ET.Element(W + "document"),
            "SOURCE",
            literal_tokens={"SOURCELT": "<", "SOURCEGT": ">", "SOURCEAMP": "&"},
        )
        encoded = "SOURCELTspeedsSOURCEGT SOURCEAMPcopy;"
        markdown = f"{encoded}\n\n`{encoded}`\n\n```text\n{encoded}\n```\n\n<div>{encoded}</div>\n"
        pandoc = pinned_pandoc()

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

    def test_literal_backticks_backslashes_and_hashes_stay_text(self) -> None:
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
        pandoc = pinned_pandoc()

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

    def test_word_line_breaks_become_paragraph_breaks_inside_blocks(self) -> None:
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
        pandoc = pinned_pandoc()

        result = _recovery.restore_literal_text(markdown, source, pandoc)

        assert "first line\n\nsecond line\n" in result
        assert "# head two\n" in result
        assert "\nedge\n" in result
        assert re.search(r"\| a b +\| c +\|", result)
        assert "SOURCE" not in result
        assert (source.restored_breaks, source.heading_breaks) == (1, 1)


class BackslashTests(unittest.TestCase):
    def test_backslashes_before_punctuation_survive_the_commonmark_writer(self) -> None:
        pandoc = pinned_pandoc()
        texts = ["a\\*b", "c\\_d and e_ f", "\\\\server\\share", "end\\", "x\\`y` z", "C:\\tools"]
        for text in texts:
            with self.subTest(text=text):
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
                assert ("&#92;" in written) == (text != "C:\\tools")
