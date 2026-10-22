"""Excel and CSV tables as Markdown: literal cell values, and checked CSV rows."""

from __future__ import annotations

import csv
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import openpyxl
import pytest
from typing_extensions import override

from md_doc_converter.converters import csv as csv_to_md
from md_doc_converter.converters import excel as excel_to_csv
from md_doc_converter.markdown.tables import literal_cell, table_row

# The tables must read the same with a GFM reader and with the renderer's.
READERS = [
    pytest.param("gfm+raw_html", id="gfm"),
    pytest.param(
        "markdown-implicit_figures-yaml_metadata_block+gfm_auto_identifiers", id="renderer"
    ),
]


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


def rendered_table(markdown: str, pandoc: str, reader: str) -> TableContents:
    """The cells, and any markup in them, of Markdown that Pandoc reads with reader as HTML."""
    result = subprocess.run(
        [pandoc, f"--from={reader}", "--to=html5", "--wrap=none"],
        input=markdown,
        capture_output=True,
        text=True,
        check=True,
    )
    table = TableContents()
    table.feed(result.stdout)
    return table


@pytest.mark.parametrize("reader", READERS)
def test_literal_cells_do_not_create_markup_or_extra_columns(reader: str, pandoc: str) -> None:
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
    assert "| _ctl_s_ready |" in markdown
    table = rendered_table(markdown, pandoc, reader)
    assert table.cells == values
    assert table.markup == []


@pytest.mark.parametrize("reader", READERS)
def test_excel_csv_markdown_retains_literal_values(
    reader: str, pandoc: str, tmp_path: Path
) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Pins"
    values = ["<mask> &copy; `literal`", r"left\|right", "_ctl_s_ready"]
    sheet.append(["Signal"])
    for value in values:
        sheet.append([value])
    source = tmp_path / "signal's values.xlsx"
    workbook.save(source)
    workbook.close()
    excel_to_csv.excel_to_csv(str(source))
    table = rendered_table(csv_to_md.csv_to_md(tmp_path / "Pins.csv"), pandoc, reader)
    assert table.cells == values
    assert table.markup == []


def test_extra_cells_fail_before_existing_output_is_changed(tmp_path: Path) -> None:
    source = tmp_path / "signals.csv"
    source.write_text("Name\nsignal,do not discard\n", encoding="utf-8")
    output = source.with_suffix(".md")
    output.write_text("existing markdown", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "md_doc_converter", "csv-to-md", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "row 2 has 2 cells but the header has 1" in result.stderr
    assert "Traceback" not in result.stderr
    assert output.read_text(encoding="utf-8") == "existing markdown"


def test_short_rows_are_padded_and_dry_run_creates_no_output_directory(tmp_path: Path) -> None:
    source = tmp_path / "signals.csv"
    with source.open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerows([["Name", "Value"], ["signal"]])
    output = tmp_path / "not-created"
    csv_to_md.convert(str(source), str(output), dry_run=True)
    assert not output.exists()
    assert "| signal | |" in csv_to_md.csv_to_md(source)
