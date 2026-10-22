"""Excel and CSV tables as Markdown: literal cell values, and checked CSV rows."""

from __future__ import annotations

import csv
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

import openpyxl
import pytest
from typing_extensions import override

from md_doc_converter.__main__ import main
from md_doc_converter.converters import csv as csv_to_md
from md_doc_converter.converters import excel as excel_to_csv
from md_doc_converter.converters import packages
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


def test_excel_to_csv_writes_one_csv_per_worksheet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workbook = openpyxl.Workbook()
    first = workbook.active
    assert first is not None
    first.title = "Pins"
    first.append(["Signal", "Width"])
    first.append(["ready", 1])
    second = workbook.create_sheet("Clocks & Resets")
    second.append(["Name", None, "=1+1"])
    source = tmp_path / "workbook.xlsx"
    workbook.save(source)
    workbook.close()
    out = tmp_path / "csv"

    assert main(["excel-to-csv", str(source), "-o", str(out)]) == 0

    assert sorted(path.name for path in out.iterdir()) == ["Clocks & Resets.csv", "Pins.csv"]
    assert (out / "Pins.csv").read_bytes() == b"Signal,Width\r\nready,1\r\n"
    # openpyxl wrote no cached value for the formula, so the cell is empty, like the blank one.
    assert (out / "Clocks & Resets.csv").read_bytes() == b"Name,,\r\n"
    assert (
        capsys.readouterr().out
        == f"Written: {out / 'Pins.csv'}\nWritten: {out / 'Clocks & Resets.csv'}\n"
    )


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        pytest.param("missing.xlsx", None, "file not found: ", id="missing"),
        pytest.param("notes.txt", b"text", "not an Excel workbook: ", id="other-format"),
        pytest.param("damaged.xlsx", b"PK not a zip", "not an Excel workbook: ", id="damaged"),
    ],
)
def test_excel_to_csv_reports_an_unreadable_workbook(
    tmp_path: Path, name: str, content: bytes | None, message: str
) -> None:
    source = tmp_path / name
    if content is not None:
        source.write_bytes(content)
    with pytest.raises(SystemExit, match=f"^excel-to-csv: error: {message}"):
        main(["excel-to-csv", str(source), "-o", str(tmp_path / "csv")])
    assert not (tmp_path / "csv").exists()


def test_csv_to_md_converts_the_csv_files_that_git_does_not_ignore(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("ignored.csv\n", encoding="utf-8")
    for name in ("kept.csv", "ignored.csv", "nested/also kept.csv"):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text("Name\nvalue\n", encoding="utf-8")
    (tmp_path / "empty.csv").write_text("", encoding="utf-8")

    assert main(["csv-to-md", str(tmp_path)]) == 0

    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.md")) == [
        "kept.md",
        "nested/also kept.md",
    ]
    assert f"Skipped (empty): {tmp_path / 'empty.csv'}" in capsys.readouterr().out


def test_csv_to_md_reports_a_folder_without_csv_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["csv-to-md", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "No CSV files found.\n"


def test_csv_to_md_reports_a_missing_input(tmp_path: Path) -> None:
    missing = tmp_path / "missing.csv"
    with pytest.raises(SystemExit, match=f"^csv-to-md: error: not found: {missing}$"):
        main(["csv-to-md", str(missing)])


@pytest.mark.parametrize(
    ("content", "header"),
    [
        pytest.param(
            b"\xef\xbb\xbfName,Value\nalpha,1\n", "| Name | Value |", id="byte-order-mark"
        ),
        pytest.param(b"Name;Value\nalpha;1\n", "| Name | Value |", id="semicolon"),
        pytest.param(b"Name\tValue\nalpha\t1\n", "| Name | Value |", id="tab"),
        pytest.param(
            b'"Name, full";Value\n"alpha, a";1\n', "| Name, full | Value |", id="quoted-comma"
        ),
        pytest.param(b"Name;alias,Value\nx;y,1\n", "| Name;alias | Value |", id="tie-is-comma"),
        pytest.param(b"Name\nalpha\n", "| Name |", id="one-column"),
    ],
)
def test_csv_encoding_and_delimiter_are_detected(
    tmp_path: Path, content: bytes, header: str
) -> None:
    source = tmp_path / "signals.csv"
    source.write_bytes(content)
    table = [line for line in csv_to_md.csv_to_md(source).splitlines() if line.startswith("|")]
    assert table[0] == header
    assert len(table) == 3


def test_the_delimiter_option_overrides_detection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "signals.csv"
    source.write_text("Name,alias;Value\nx,y;1\n", encoding="utf-8")
    assert main(["csv-to-md", str(source), "--delimiter", "semicolon"]) == 0
    assert capsys.readouterr().out == f"Written: {source.with_suffix('.md')}\n"
    table = source.with_suffix(".md").read_text(encoding="utf-8").splitlines()[4:]
    assert table == ["| Name,alias | Value |", "| --- | --- |", "| x,y | 1 |"]


def test_excel_to_csv_rejects_a_workbook_that_expands_too_far(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "workbook.xlsx"
    workbook = openpyxl.Workbook()
    workbook.save(source)
    workbook.close()
    monkeypatch.setattr(packages, "MAX_EXPANDED_SIZE", 0)
    load = mock.Mock(side_effect=AssertionError("the workbook was opened"))
    monkeypatch.setattr(openpyxl, "load_workbook", load)
    with pytest.raises(SystemExit, match=r"^excel-to-csv: error: .*workbook\.xlsx expands to"):
        main(["excel-to-csv", str(source)])
    load.assert_not_called()
