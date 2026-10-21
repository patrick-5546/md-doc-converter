from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from md_doc_converter import pandoc
from md_doc_converter.converters.docx import navigation, reference
from md_doc_converter.converters.docx.models import ConversionError


class ParsedMarkdownTest(unittest.TestCase):
    def test_explicit_reader_is_used_and_document_is_parsed_once(self) -> None:
        completed = subprocess.CompletedProcess(["pandoc"], 0, '{"blocks":[]}', "")
        with mock.patch("subprocess.run", return_value=completed) as run:
            parsed = pandoc.parse_markdown("# Heading", reader="gfm+raw_html")
        self.assertEqual(parsed.markdown, "# Heading")
        self.assertEqual(parsed.reader, "gfm+raw_html")
        self.assertEqual(parsed.blocks, [])
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["pandoc", "--from=gfm+raw_html", "--to=json"])

    def test_ast_reuse_rejects_changed_content_or_reader(self) -> None:
        parsed = pandoc.ParsedMarkdown("# Original", "gfm+raw_html", {"blocks": []})
        for text, reader in (("# Changed", parsed.reader), (parsed.markdown, "markdown")):
            with (
                self.subTest(text=text, reader=reader),
                self.assertRaisesRegex(pandoc.PandocError, "different content or reader"),
            ):
                parsed.copy_ast(text, reader)

    def test_ast_copy_does_not_change_the_reusable_document(self) -> None:
        parsed = pandoc.ParsedMarkdown("word", "gfm+raw_html", {"blocks": [{"t": "Para", "c": []}]})
        copied = parsed.copy_ast(parsed.markdown, parsed.reader)
        blocks = copied["blocks"]
        assert isinstance(blocks, list)
        blocks.clear()
        self.assertEqual(len(parsed.blocks), 1)

    def test_invalid_json_or_root_is_an_explicit_error(self) -> None:
        for payload in ("not JSON", "[]", "{}", '{"blocks":{}}'):
            with self.subTest(payload=payload), self.assertRaises(pandoc.PandocError):
                pandoc.decode_document(payload)

    def test_docx_rewriter_rejects_a_stale_parse_before_running_tools(self) -> None:
        parsed = pandoc.ParsedMarkdown("# Original", "gfm+raw_html", {"blocks": []})
        with (
            mock.patch("subprocess.run") as run,
            self.assertRaisesRegex(ConversionError, "different content or reader"),
        ):
            navigation.rewrite_page_references(
                "# Changed", Path("out.md"), Path("/docs/out.md"), {}, "pandoc", parsed=parsed
            )
        run.assert_not_called()

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc not installed")
    def test_docx_headings_and_link_rewriting_share_one_parse(self) -> None:
        source = "# Top\n\n[Self](#top)\n"
        original_run = subprocess.run
        with mock.patch("subprocess.run", wraps=original_run) as run:
            pages = navigation.split_markdown_pages(source, Path("/docs/out.md"), 0, None, "pandoc")
        reads = [call for call in run.call_args_list if "--to=json" in call.args[0]]
        self.assertEqual(len(reads), 1)
        self.assertIn("[Self](#top)", pages[Path("out.md")])

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc not installed")
    def test_rewriting_does_not_mutate_the_parse_used_for_heading_identity(self) -> None:
        source = "# Top\n\n[Self](#top)\n"
        parsed = pandoc.parse_markdown(source, reader=reference.DOCX_READER)
        before = json.dumps(parsed.ast)
        from md_doc_converter.converters.docx.models import SectionTarget

        result = navigation.rewrite_page_references(
            source,
            Path("guide/first.md"),
            Path("/docs/guide.md"),
            {"top": SectionTarget(Path("guide/second.md"), "top")},
            "pandoc",
            parsed=parsed,
        )
        self.assertIn("(second.md#top)", result)
        self.assertEqual(json.dumps(parsed.ast), before)


class BareAddressTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pandoc"), "pandoc not installed")
    def test_bare_addresses_in_prose_become_autolinks_and_nothing_else_changes(self) -> None:
        source = (
            "# About https://a.example\n\n"
            "Mail me@b.example, see https://c.example/x_(y). or \\<d@e.example\\>.\n\n"
            "[https://linked.example](https://linked.example) `https://code.example` "
            "![https://alt.example](pic.png) <span>https://raw.example</span>\n\n"
            "    https://indented.example\n"
        )
        linked: list[int] = []
        result = navigation.rewrite_page_references(
            source, Path("page.md"), Path("/docs/page.md"), {}, "pandoc", linked=linked
        )
        self.assertEqual(linked, [5])
        self.assertIn("# About <https://a.example>\n", result)
        self.assertIn(
            "Mail <me@b.example>, see <https://c.example/x_(y)>. or <d@e.example>.", result
        )
        # Text between inline HTML tags is still Markdown prose.
        self.assertIn("<span><https://raw.example></span>", result)
        for unchanged in (
            "<https://linked.example>",
            "`https://code.example`",
            "![https://alt.example](pic.png)",
            "    https://indented.example",
        ):
            self.assertIn(unchanged, result)
