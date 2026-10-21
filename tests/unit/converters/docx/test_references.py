"""Synthetic DOCX regression coverage."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from typing_extensions import override

from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import publication as _publication
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import tables as _tables
from md_doc_converter.converters.docx import validation as _validation
from md_doc_converter.pandoc import JSONValue
from tests.support.docx import (
    W,
)


class LinkValidationTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="docx-link-validation-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = self.root / "input.docx"
        source.write_bytes(b"source")
        self.paths = _publication.validate_paths(source, self.root / "guide.md")
        self.staged_images = self.root / "staged-images"
        self.staged_images.mkdir()
        self.pandoc = _reference.require_pandoc()

    def validate_external_link(self, expected: str, rendered: str) -> list[_validation.Issue]:
        source = _models.SourceDocument(ET.Element(W + "document"), "test", link_targets=[expected])
        link = ET.Element("a", {"href": rendered})
        link.text = "Link"
        return _validation.validate_outputs(
            {Path("guide.md"): _reference.html_element(link)},
            self.paths,
            self.staged_images,
            self.pandoc,
            source,
        )

    def test_rendered_parentheses_and_unicode_match_recovered_urls(self) -> None:
        for expected, rendered in (
            ("https://example.test/topic_(detail)", "https://example.test/topic_%28detail%29"),
            ("https://example.test/R\u00e9sum\u00e9", "https://example.test/R%C3%A9sum%C3%A9"),
            (
                "https://example.test/?q=caf\u00e9#r\u00e9sum\u00e9",
                "https://example.test/?q=caf%C3%A9#r%C3%A9sum%C3%A9",
            ),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(self.validate_external_link(expected, rendered), [])

    def test_distinct_reserved_delimiters_and_percent_sequences_are_not_equivalent(self) -> None:
        for expected, rendered in (
            ("https://example.test/a/b", "https://example.test/a%2Fb"),
            ("https://example.test/a?x=y", "https://example.test/a%3Fx=y"),
            ("https://example.test/a#b", "https://example.test/a%23b"),
            ("https://example.test/?a=b&c=d", "https://example.test/?a=b%26c=d"),
            ("https://example.test/?a=b=c", "https://example.test/?a=b%3Dc"),
            ("https://example.test/?q=a+b", "https://example.test/?q=a%20b"),
            ("https://example.test/100%25", "https://example.test/100%2525"),
            ("https://example.test/%252F", "https://example.test/%2F"),
            ("https://example.test/?a=b&copy;", "https://example.test/?a=b%C2%A9"),
        ):
            with self.subTest(expected=expected):
                details = [
                    detail for _reason, detail in self.validate_external_link(expected, rendered)
                ]
                self.assertTrue(
                    any(
                        "do not preserve every recovered Word hyperlink" in detail
                        for detail in details
                    ),
                    details,
                )

    def test_markdown_emission_preserves_entity_shaped_query_text(self) -> None:
        target = "https://example.test/?q=one&copy;=two&notit=three"
        link = ET.Element("a", {"href": target})
        link.text = "Query"

        rendered = _tables.table_inline(link)

        self.assertIsNotNone(rendered)
        assert rendered is not None
        self.assertEqual(
            _validation.collect_references(_reference.pandoc_html(rendered, self.pandoc)).links,
            [target],
        )


class ParagraphSplitTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.pandoc = _reference.require_pandoc()

    def split(self, markdown: str, filler: str | None = None) -> str:
        tree = _reference.markdown_ast(markdown, self.pandoc, "Paragraph split test").ast
        result = _reference.split_paragraphs_at_line_breaks(
            tree, lambda item: filler is not None and item == {"t": "Str", "c": filler}
        )
        return _reference.run_command(
            [self.pandoc, "--from=json", "--to=gfm+raw_html", "--wrap=none"],
            "Paragraph split test",
            input_text=json.dumps(result),
        )

    def test_line_breaks_become_paragraph_breaks(self) -> None:
        cases = {
            "Line one\\\nLine two": "Line one\n\nLine two\n",
            "**Bold\\\ntext** after": "**Bold**\n\n**text** after\n",
            "**a \\\nb**": "**a**\n\n**b**\n",
            "[Link\\\ntext](https://example.test/)": (
                "[Link](https://example.test/)\n\n[text](https://example.test/)\n"
            ),
            "- Item\\\n  continued\n- Next": "- Item\n\n  continued\n\n- Next\n",
            "a\\\n\\\nb": "a\n\nb\n",
        }
        for markdown, expected in cases.items():
            with self.subTest(markdown=markdown):
                self.assertEqual(self.split(markdown), expected)

    def test_pieces_with_only_filler_stay_with_a_neighbour(self) -> None:
        cases = {
            "text\\\nMARK": "textMARK\n",
            "MARK\\\ntext": "MARKtext\n",
            "**x MARK\\\ny**": "**x** MARK\n\n**y**\n",
            "**MARK\\\ny**": "MARK**y**\n",
        }
        for markdown, expected in cases.items():
            with self.subTest(markdown=markdown):
                self.assertEqual(self.split(markdown, "MARK"), expected)

    def test_headings_and_tables_keep_their_line_breaks(self) -> None:
        line: JSONValue = [{"t": "Str", "c": "a"}, {"t": "LineBreak"}, {"t": "Str", "c": "b"}]
        heading: JSONValue = {"t": "Header", "c": [1, ["", [], []], line]}
        table: JSONValue = {"t": "Table", "c": [["", [], []], [None, []], [], [], [], [line]]}
        blocks: JSONValue = [heading, table]

        self.assertEqual(_reference.split_paragraphs_at_line_breaks(blocks), blocks)


class TableCodePipeTests(unittest.TestCase):
    def test_pipes_in_table_code_spans_stay_escaped_through_the_gfm_writer(self) -> None:
        pandoc = _reference.require_pandoc()
        markdown = "| A | B |\n| --- | --- |\n| `a\\|b` | ``x`\\|`y`` |\n\nOutside `c|d`.\n"
        tree = _reference.markdown_ast(markdown, pandoc, "Table code test").ast
        written = _reference.run_command(
            [pandoc, "--from=json", "--to=gfm+raw_html", "--wrap=none"],
            "Table code test",
            input_text=json.dumps(_reference.protect_backslashes(tree)),
        )
        self.assertIn("`a\\|b`", written)
        self.assertIn("``x`\\|`y``", written)
        self.assertIn("Outside `c|d`.", written)
        self.assertEqual(_reference.markdown_ast(written, pandoc, "Table code test").ast, tree)
