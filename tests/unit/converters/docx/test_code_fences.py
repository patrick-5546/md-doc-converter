"""Code fences the converter writes itself, independent of Pandoc's GFM writer."""

from __future__ import annotations

import json
import unittest

from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.pandoc import JSONValue


def code(
    text: str, classes: list[str], *, identifier: str = "", pairs: list[JSONValue] | None = None
) -> dict[str, JSONValue]:
    return {"t": "CodeBlock", "c": [[identifier, list(classes), pairs or []], text]}


def para(text: str) -> dict[str, JSONValue]:
    return {"t": "Para", "c": [{"t": "Str", "c": text}]}


def raw(text: str) -> dict[str, JSONValue]:
    return {"t": "RawBlock", "c": ["gfm", text]}


class CodeFenceTest(unittest.TestCase):
    def test_fence_outlasts_any_backtick_run_that_starts_a_line(self) -> None:
        cases = (
            ("x = 1", "text", "```text\nx = 1\n```"),
            ("", "text", "```text\n```"),
            ("a\n```\nb", "", "````\na\n```\nb\n````"),
            ("   ````js\nb", "text", "`````text\n   ````js\nb\n`````"),
            ("    ```\nindented four", "text", "```text\n    ```\nindented four\n```"),
            ("inline ``` run", "text", "```text\ninline ``` run\n```"),
        )
        for text, info, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(_reference.code_fence(text, info), expected)


class FenceCodeBlocksTest(unittest.TestCase):
    def test_labels_survive_and_unlabeled_blocks_stay_with_pandoc(self) -> None:
        plain = code("plain", [])
        blocks: list[JSONValue] = [
            code("x = 1", ["text"]),
            code("module m;", ["systemverilog-ams", "other"]),
            plain,
            code("```\ninside", ["text"]),
        ]
        self.assertEqual(
            _reference.fence_code_blocks(blocks),
            [
                raw("```text\nx = 1\n```"),
                raw("```systemverilog-ams\nmodule m;\n```"),
                plain,
                raw("````text\n```\ninside\n````"),
            ],
        )

    def test_blocks_the_writer_may_need_as_html_stay_with_pandoc(self) -> None:
        kept: list[JSONValue] = [
            code("x", ["text"], identifier="listing"),
            code("x", ["text"], pairs=[["data-line", "3"]]),
            code("x", ["a`b"]),
            code("x", ["two words"]),
        ]
        self.assertEqual(_reference.fence_code_blocks(kept), kept)
        cell: JSONValue = [["", [], []], {"t": "AlignDefault"}, 1, 1, [code("x", ["text"])]]
        table: JSONValue = {
            "t": "Table",
            "c": [
                ["", [], []],
                [None, []],
                [],
                [["", [], []], []],
                [[["", [], []], 0, [], [[["", [], []], [cell]]]]],
                [["", [], []], []],
            ],
        }
        definitions: JSONValue = {
            "t": "DefinitionList",
            "c": [[[{"t": "Str", "c": "Term"}], [[code("x", ["text"])]]]],
        }
        figure: JSONValue = {"t": "Figure", "c": [["", [], []], [None, []], [code("x", ["text"])]]}
        for name, block in (("Table", table), ("DefinitionList", definitions), ("Figure", figure)):
            with self.subTest(block=name):
                self.assertEqual(_reference.fence_code_blocks([block]), [block])

    def test_blocks_in_lists_quotes_and_divs_are_fenced(self) -> None:
        fenced = raw("```text\nx\n```")
        blocks: list[JSONValue] = [
            {"t": "BulletList", "c": [[para("Item"), code("x", ["text"])], [para("Next")]]},
            {
                "t": "OrderedList",
                "c": [[3, {"t": "Decimal"}, {"t": "Period"}], [[code("x", ["text"])]]],
            },
            {"t": "BlockQuote", "c": [para("Quote"), code("x", ["text"])]},
            {"t": "Div", "c": [["", ["note"], []], [code("x", ["text"])]]},
        ]
        document: JSONValue = {"pandoc-api-version": [1, 23], "meta": {}, "blocks": blocks}
        self.assertEqual(
            _reference.fence_code_blocks(document),
            {
                "pandoc-api-version": [1, 23],
                "meta": {},
                "blocks": [
                    {"t": "BulletList", "c": [[para("Item"), fenced], [para("Next")]]},
                    {
                        "t": "OrderedList",
                        "c": [[3, {"t": "Decimal"}, {"t": "Period"}], [[fenced]]],
                    },
                    {"t": "BlockQuote", "c": [para("Quote"), fenced]},
                    {"t": "Div", "c": [["", ["note"], []], [fenced]]},
                ],
            },
        )

    def test_pandoc_writes_the_fence_and_reads_the_label_back(self) -> None:
        pandoc = _reference.require_pandoc()
        document: JSONValue = {
            "pandoc-api-version": [1, 23, 1],
            "meta": {},
            "blocks": [
                para("Before."),
                {"t": "BulletList", "c": [[para("Item"), code("x = 1\n  y", ["text"])]]},
            ],
        }
        written = _reference.run_command(
            [pandoc, "--from=json", "--to=gfm+raw_html", "--wrap=none"],
            "Code fence writing",
            input_text=json.dumps(_reference.fence_code_blocks(document)),
        )
        self.assertIn("- Item\n\n  ```text\n  x = 1\n    y\n  ```\n", written)
        reread = _reference.markdown_ast(written, pandoc, "Code fence reading").blocks
        self.assertIn(
            code("x = 1\n  y", ["text"]),
            json.loads(json.dumps(reread))[1]["c"][0],
        )


if __name__ == "__main__":
    unittest.main()
