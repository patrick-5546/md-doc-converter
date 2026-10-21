"""Synthetic DOCX regression coverage."""

from __future__ import annotations

import json
import unittest
from xml.etree import ElementTree as ET

from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import recovery as _recovery
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.pandoc import JSONValue
from tests.support.docx import (
    W,
)


class LiteralTextTests(unittest.TestCase):
    def test_literal_recovery_distinguishes_prose_code_and_generated_html(self) -> None:
        source = _models.SourceDocument(
            ET.Element(W + "document"),
            "SOURCE",
            literal_tokens={"SOURCELT": "<", "SOURCEGT": ">", "SOURCEAMP": "&"},
        )
        encoded = "SOURCELTspeedsSOURCEGT SOURCEAMPcopy;"
        markdown = f"{encoded}\n\n`{encoded}`\n\n```text\n{encoded}\n```\n\n<div>{encoded}</div>\n"
        pandoc = _reference.require_pandoc()

        result = _recovery.restore_literal_text(markdown, source, pandoc)
        parsed = _reference.pandoc_html(result, pandoc)

        self.assertNotIn("SOURCE", result)
        self.assertFalse(list(parsed.iter("speeds")))
        self.assertEqual("".join(parsed.itertext()).count("<speeds> &copy;"), 4)
        self.assertEqual(
            ["".join(node.itertext()).strip() for node in parsed.iter("code")],
            ["<speeds> &copy;", "<speeds> &copy;"],
        )
        self.assertTrue(list(parsed.iter("div")))

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
        pandoc = _reference.require_pandoc()

        result = _recovery.restore_literal_text(markdown, source, pandoc)
        parsed = _reference.pandoc_html(result, pandoc)

        self.assertNotIn("SOURCE", result)
        self.assertEqual(
            [" ".join("".join(node.itertext()).split()) for node in parsed.iter("p")],
            [
                "Use `uvm_info and `uvm_error.",
                "# of entries",
                "Path C:\\tools\\a and \\*b, \\\\server",
            ],
        )
        self.assertFalse(list(parsed.iter("code")))
        self.assertFalse(list(parsed.iter("h1")))

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
        pandoc = _reference.require_pandoc()

        result = _recovery.restore_literal_text(markdown, source, pandoc)

        self.assertIn("first line\n\nsecond line\n", result)
        self.assertIn("# head two\n", result)
        self.assertIn("\nedge\n", result)
        self.assertRegex(result, r"\| a b +\| c +\|")
        self.assertNotIn("SOURCE", result)
        self.assertEqual((source.restored_breaks, source.heading_breaks), (1, 1))


class BackslashTests(unittest.TestCase):
    def test_backslashes_before_punctuation_survive_the_commonmark_writer(self) -> None:
        pandoc = _reference.require_pandoc()
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
                self.assertEqual("".join(parsed.itertext()).strip(), text)
                self.assertEqual("&#92;" in written, text != "C:\\tools")
