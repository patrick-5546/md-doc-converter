from __future__ import annotations

import contextlib
import csv
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

import openpyxl
from typing_extensions import override

from md_doc_converter.converters import csv as csv_to_md
from md_doc_converter.converters import excel as excel_to_csv
from md_doc_converter.markdown.tables import literal_cell, table_row

PANDOC = shutil.which("pandoc")
READERS = ("gfm+raw_html", "markdown-implicit_figures-yaml_metadata_block+gfm_auto_identifiers")


class TableContents(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.cells: list[str] = []
        self.current: list[str] | None = None
        self.markup: list[str] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "td":
            self.current = []
        elif self.current is not None:
            if tag == "br":
                self.current.append("\n")
            else:
                self.markup.append(tag)

    @override
    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.append(data)

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.current is not None:
            self.cells.append("".join(self.current))
            self.current = None


@unittest.skipUnless(PANDOC, "pandoc not installed")
class LiteralTableTests(unittest.TestCase):
    def assert_cells(self, markdown: str, expected: list[str]) -> None:
        for reader in READERS:
            with self.subTest(reader=reader):
                result = subprocess.run(
                    [PANDOC or "pandoc", f"--from={reader}", "--to=html5", "--wrap=none"],
                    input=markdown,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                parser = TableContents()
                parser.feed(result.stdout)
                self.assertEqual(parser.cells, expected)
                self.assertEqual(parser.markup, [])

    def test_literal_cells_do_not_create_markup_or_extra_columns(self) -> None:
        values = [
            "<mask> &copy; `literal`",
            r"left\|right",
            r"two\\|pipes | literal",
            "_ctl_s_ready",
            "_emphasis_ and __strong__",
            "*emphasis* ~~strike~~ ~subscript~ ^superscript^ $math$",
            """'single' "double" -- --- ...""",
            "[link](target) [ref] [0] [3:0]",
            "first\nsecond",
            r"\_literal\_ and \*literal\*",
        ]
        markdown = table_row(["Value"]) + "\n" + table_row(["---"]) + "\n"
        markdown += "\n".join(table_row([literal_cell(value)]) for value in values)
        markdown += "\n\n[ref]: https://example.test\n[0]: target\n"
        self.assertIn("| _ctl_s_ready |", markdown)
        self.assert_cells(markdown, values)

    def test_excel_csv_markdown_retains_literal_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            assert sheet is not None
            sheet.title = "Pins"
            values = ["<mask> &copy; `literal`", r"left\|right", "_ctl_s_ready"]
            sheet.append(["Signal"])
            for value in values:
                sheet.append([value])
            source = root / "signal's values.xlsx"
            workbook.save(source)
            workbook.close()
            with contextlib.redirect_stdout(io.StringIO()):
                excel_to_csv.excel_to_csv(str(source))
            self.assert_cells(csv_to_md.csv_to_md(root / "Pins.csv"), values)


class CSVValidationTests(unittest.TestCase):
    def test_extra_cells_fail_before_existing_output_is_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "signals.csv"
            source.write_text("Name\nsignal,do not discard\n", encoding="utf-8")
            output = source.with_suffix(".md")
            output.write_text("existing markdown", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "md_doc_converter", "csv-to-md", str(source)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("row 2 has 2 cells but the header has 1", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(output.read_text(), "existing markdown")

    def test_short_rows_are_padded_and_dry_run_creates_no_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "signals.csv"
            with source.open("w", newline="", encoding="utf-8") as stream:
                csv.writer(stream).writerows([["Name", "Value"], ["signal"]])
            output = Path(temporary) / "not-created"
            with contextlib.redirect_stdout(io.StringIO()):
                csv_to_md.convert(str(source), str(output), dry_run=True)
            self.assertFalse(output.exists())
            self.assertIn("| signal | |", csv_to_md.csv_to_md(source))


if __name__ == "__main__":
    unittest.main()
