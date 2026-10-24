"""Excel and CSV tables as Markdown: literal cell values, worksheets, and checked CSV rows."""

from __future__ import annotations

import csv
import re
import subprocess
import sys
import tempfile
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import override
from unittest import mock

import openpyxl
import pytest
from hypothesis import given
from hypothesis import strategies as st
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Font

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
    """The text of each cell, and the markup in cells other than line breaks and autolinks.

    GFM links a web or e-mail address written as text, as GitHub does in prose; the cell still
    shows the text as written.
    """

    def __init__(self) -> None:
        super().__init__()
        self.headers: list[str] = []
        self.cells: list[str] = []
        self.current: list[str] | None = None
        self.markup: list[str] = []
        self.link: tuple[str, int] | None = None

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"td", "th"}:
            self.current = []
        elif self.current is not None:
            if tag == "br":
                self.current.append("\n")
            elif tag == "a" and self.link is None:
                self.link = (dict(attrs).get("href") or "", len(self.current))
            else:
                self.markup.append(tag)

    @override
    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.append(data)

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.link is not None and self.current is not None:
            target, start = self.link
            shown = "".join(self.current[start:])
            if target not in {shown, "mailto:" + shown, "http://" + shown}:
                self.markup.append("a")
            self.link = None
        elif tag in {"td", "th"} and self.current is not None:
            (self.cells if tag == "td" else self.headers).append("".join(self.current))
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
        "\U00013460@0 and _x_\U000323b0",
    ]
    markdown = table_row(["Value"]) + "\n" + table_row(["---"]) + "\n"
    markdown += "\n".join(table_row([literal_cell(value)]) for value in values)
    markdown += "\n\n[ref]: https://example.test\n[0]: target\n"
    assert "| _ctl_s_ready |" in markdown
    table = rendered_table(markdown, pandoc, reader)
    assert table.cells == values
    assert table.markup == []


@pytest.mark.parametrize(
    ("text", "written"),
    [
        pytest.param('it\'s "quoted"', 'it\\\'s \\"quoted\\"', id="quotes"),
        pytest.param("user@example.com and @mention", "user@example.com and \\@mention", id="at"),
        pytest.param("12:30, :smile: and a:b:c", "12:30, \\:smile: and a\\:b:c", id="colons"),
        pytest.param("snake_case_name and _x_", "snake_case_name and _x\\_", id="underscores"),
        pytest.param("<b> & \u00a9", "&lt;b&gt; &amp; \u00a9", id="html"),
        # Letters of Unicode 16 and 17, which Pandoc reads as symbols, and a letter of Unicode
        # 3.2 that is a mark now.
        pytest.param(
            "\U00013460@0 \U000323b0@0 _x_\U00013460 \u1885@0",
            "\U00013460\\@0 \U000323b0\\@0 _x\\_\U00013460 \u1885\\@0",
            id="letters-unknown-to-pandoc",
        ),
    ],
)
def test_literal_cells_escape_only_what_would_be_read_as_markup(text: str, written: str) -> None:
    assert literal_cell(text) == written


# Text a cell may hold: letters, digits, punctuation, symbols, spaces and line breaks.
CELL_TEXT = st.text(
    st.characters(categories=("L", "N", "P", "S")) | st.sampled_from(" \n"), max_size=30
)


def shown(text: str) -> str:
    """Cell text as a reader shows it: trimmed, a line break for each line ending, a space for
    each tab, runs of spaces as one, and in NFC, the form Pandoc's GFM reader gives text. The
    variation selector that Pandoc's HTML writer puts after ↔ and ↩, so that they show as text
    rather than emoji, is left out."""
    text = text.replace("\N{VARIATION SELECTOR-15}", "")
    text = text.strip().replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    return unicodedata.normalize("NFC", re.sub(r" {2,}", " ", text))


@pytest.mark.parametrize("reader", READERS)
@given(values=st.lists(CELL_TEXT, min_size=1, max_size=8))
def test_any_cell_text_reads_back_literally(reader: str, pandoc: str, values: list[str]) -> None:
    markdown = table_row(["Value"]) + "\n" + table_row(["---"]) + "\n"
    markdown += "\n".join(table_row([literal_cell(value)]) for value in values) + "\n"
    table = rendered_table(markdown, pandoc, reader)
    assert [shown(cell) for cell in table.cells] == [shown(value) for value in values]
    assert table.markup == []


# CSV cells may also hold tabs and carriage returns, which a CSV file quotes.
CSV_CELL = st.text(
    st.characters(categories=("L", "N", "P", "S")) | st.sampled_from(" \t\r\n"), max_size=20
)
# The column names a reader shows a header row for: some text besides spaces.
COLUMN_NAME = CSV_CELL.filter(lambda text: shown(text) != "")


@st.composite
def csv_tables(draw: st.DrawFn, names: st.SearchStrategy[str] = COLUMN_NAME) -> list[list[str]]:
    """A header row and up to four rows as wide."""
    width = draw(st.integers(1, 4))
    header = draw(st.lists(names, min_size=width, max_size=width))
    rows = draw(st.lists(st.lists(CSV_CELL, min_size=width, max_size=width), max_size=4))
    return [header, *rows]


def write_csv(path: Path, rows: list[list[str]], delimiter: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        csv.writer(file, delimiter=delimiter).writerows(rows)


@pytest.mark.parametrize("reader", READERS)
@given(table=csv_tables(), delimiter=st.sampled_from(list(csv_to_md.DELIMITERS.values())))
def test_any_csv_rows_read_back_through_both_readers(
    reader: str, pandoc: str, table: list[list[str]], delimiter: str
) -> None:
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "table.csv"
        write_csv(source, table, delimiter)
        markdown = csv_to_md.csv_to_md(source, delimiter)
    contents = rendered_table(markdown, pandoc, reader)
    assert [shown(cell) for cell in contents.headers] == [shown(cell) for cell in table[0]]
    assert [shown(cell) for cell in contents.cells] == [
        shown(cell) for row in table[1:] for cell in row
    ]
    assert contents.markup == []


@given(
    table=csv_tables(CSV_CELL.filter(lambda text: not set(text) & set(",;\t"))).filter(
        lambda table: len(table[0]) > 1
    ),
    delimiter=st.sampled_from(list(csv_to_md.DELIMITERS.values())),
)
def test_the_delimiter_is_detected_from_a_header_of_several_columns(
    table: list[list[str]], delimiter: str
) -> None:
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "table.csv"
        write_csv(source, table, delimiter)
        assert csv_to_md.detect_delimiter(source.read_text(encoding="utf-8")) == delimiter


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


def save_workbook(path: Path) -> Path:
    """Save a workbook of two tables, an empty worksheet and a chart sheet. A formatted cell
    past the first table makes that worksheet's extent larger than the table."""
    workbook = openpyxl.Workbook()
    pins = workbook.active
    assert pins is not None
    pins.title = "Pin-Assignments"
    pins.append(["Signal", "Width", "Note"])
    pins.append(["ready", 1, None])
    pins.append(["<mask> | `x`", 2.5, "two\nlines"])
    pins["E9"].font = Font(bold=True)
    workbook.create_sheet("Empty")
    clocks = workbook.create_sheet("Clocks & Resets")
    clocks.append(["Name", "=1+1", "Edge"])
    chart = BarChart()
    chart.add_data(Reference(pins, min_col=2, min_row=1, max_row=3), titles_from_data=True)
    workbook.create_chartsheet("Chart").add_chart(chart)
    workbook.save(path)
    workbook.close()
    return path


def test_excel_to_csv_writes_a_csv_file_for_each_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = save_workbook(tmp_path / "pins.xlsx")
    out = tmp_path / "csv"

    assert main(["excel-to-csv", str(source), "-o", str(out)]) == 0

    assert sorted(path.name for path in out.iterdir()) == [
        "Clocks & Resets.csv",
        "Pin-Assignments.csv",
    ]
    assert (out / "Pin-Assignments.csv").read_bytes() == (
        b'Signal,Width,Note\r\nready,1,\r\n<mask> | `x`,2.5,"two\nlines"\r\n'
    )
    # openpyxl wrote no cached value for the formula, so its cell is empty.
    assert (out / "Clocks & Resets.csv").read_bytes() == b"Name,,Edge\r\n"
    assert capsys.readouterr().out == (
        f"Written: {out / 'Pin-Assignments.csv'}\nSkipped (empty): Empty\n"
        f"Written: {out / 'Clocks & Resets.csv'}\n"
    )


def test_excel_to_md_writes_the_pages_csv_to_md_writes_from_excel_to_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = save_workbook(tmp_path / "pins.xlsx")
    pages = tmp_path / "md"

    assert main(["excel-to-md", str(source), "-o", str(pages)]) == 0

    assert capsys.readouterr().out == (
        f"Written: {pages / 'Pin-Assignments.md'}\nSkipped (empty): Empty\n"
        f"Written: {pages / 'Clocks & Resets.md'}\n"
    )
    assert (pages / "Pin-Assignments.md").read_text(encoding="utf-8") == (
        "# Pin Assignments\n\n"
        "> Generated from the Pin-Assignments worksheet of pins.xlsx using md-doc-converter "
        "(excel-to-md) — do not manually modify\n\n"
        "| Signal | Width | Note |\n| --- | --- | --- |\n| ready | 1 | |\n"
        "| &lt;mask&gt; \\| \\`x\\` | 2.5 | two<br>lines |\n"
    )
    # The name stays text, here an ampersand, which could start an entity such as &copy;.
    assert (pages / "Clocks & Resets.md").read_text(encoding="utf-8").splitlines()[:3] == [
        "# Clocks &amp; Resets",
        "",
        (
            "> Generated from the Clocks &amp; Resets worksheet of pins.xlsx using "
            "md-doc-converter (excel-to-md) — do not manually modify"
        ),
    ]
    assert main(["excel-to-csv", str(source), "-o", str(tmp_path / "csv")]) == 0
    assert main(["csv-to-md", str(tmp_path / "csv")]) == 0
    names = sorted(path.name for path in pages.iterdir())
    assert names == ["Clocks & Resets.md", "Pin-Assignments.md"]
    for name in names:
        direct = (pages / name).read_text(encoding="utf-8").splitlines()
        two_steps = (tmp_path / "csv" / name).read_text(encoding="utf-8").splitlines()
        # Only the notes, which name the sources, differ.
        assert direct[:2] + direct[3:] == two_steps[:2] + two_steps[3:]
        # Pages have LF line endings on every system.
        assert b"\r" not in (pages / name).read_bytes() + (tmp_path / "csv" / name).read_bytes()


@pytest.mark.parametrize("command", ["excel-to-md", "excel-to-csv"])
@pytest.mark.parametrize("name", ["a<b", 'say "hi"', "a|b", "CON", "nul", "COM1"])
def test_a_worksheet_that_cannot_name_a_file_on_windows_is_refused(
    command: str, name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(excel_to_csv, "WINDOWS", True)
    workbook = openpyxl.Workbook()
    fine = workbook.active
    assert fine is not None
    fine.title = "Fine"
    fine.append(["Name"])
    workbook.create_sheet(name).append(["Name"])
    workbook.save(tmp_path / "book.xlsx")
    workbook.close()
    with pytest.raises(SystemExit, match=f"the worksheet {re.escape(repr(name))} cannot name a"):
        main([command, str(tmp_path / "book.xlsx")])
    # Not even the other worksheet's file is written.
    assert [path.name for path in tmp_path.iterdir()] == ["book.xlsx"]


def test_excel_to_md_dry_run_names_the_pages_beside_the_workbook(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = save_workbook(tmp_path / "pins.xlsx")

    assert main(["excel-to-md", str(source), "--dry-run"]) == 0

    assert capsys.readouterr().out == (
        f"Would write: {tmp_path / 'Pin-Assignments.md'}\nSkipped (empty): Empty\n"
        f"Would write: {tmp_path / 'Clocks & Resets.md'}\n"
    )
    assert sorted(path.name for path in tmp_path.iterdir()) == ["pins.xlsx"]


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        pytest.param("missing.xlsx", None, "file not found: ", id="missing"),
        pytest.param("notes.txt", b"text", "not an Excel workbook: ", id="other-format"),
        pytest.param("damaged.xlsx", b"PK not a zip", "not an Excel workbook: ", id="damaged"),
        pytest.param(
            "protected.xlsx",
            bytes.fromhex("d0cf11e0a1b11ae1") + b"EncryptedPackage",
            r".*protected\.xlsx is encrypted, or in the Excel 97-2003 format; convert an "
            r"unencrypted \.xlsx copy$",
            id="encrypted",
        ),
    ],
)
@pytest.mark.parametrize("command", ["excel-to-md", "excel-to-csv"])
def test_an_unreadable_workbook_is_reported(
    tmp_path: Path, command: str, name: str, content: bytes | None, message: str
) -> None:
    source = tmp_path / name
    if content is not None:
        source.write_bytes(content)
    with pytest.raises(SystemExit, match=f"^{command}: error: {message}"):
        main([command, str(source), "-o", str(tmp_path / "out")])
    assert not (tmp_path / "out").exists()


def test_csv_to_md_converts_the_csv_files_that_git_does_not_ignore(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("ignored.csv\n", encoding="utf-8")
    # Git quotes a name that is not ASCII, unless it separates the names with NULs.
    for name in ("kept.csv", "ignored.csv", "nested/also kept.csv", "r\u00e9sum\u00e9.csv"):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text("Name\nvalue\n", encoding="utf-8")
    (tmp_path / "empty.csv").write_text("", encoding="utf-8")

    assert main(["csv-to-md", str(tmp_path)]) == 0

    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.md")) == [
        "kept.md",
        "nested/also kept.md",
        "r\u00e9sum\u00e9.md",
    ]
    assert f"Skipped (empty): {tmp_path / 'empty.csv'}" in capsys.readouterr().out


def test_csv_to_md_reports_a_folder_without_csv_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["csv-to-md", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "No CSV files found.\n"


def test_csv_to_md_reports_a_missing_input(tmp_path: Path) -> None:
    missing = tmp_path / "missing.csv"
    message = f"^csv-to-md: error: not found: {re.escape(str(missing))}$"
    with pytest.raises(SystemExit, match=message):
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
        pytest.param(
            b"Name;Price, EUR;Count\nx;1,5;2\n",
            "| Name | Price, EUR | Count |",
            id="semicolon-outnumbers-a-comma",
        ),
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


def test_csv_to_md_titles_the_table_by_its_file_name(tmp_path: Path) -> None:
    source = tmp_path / "pin-assignments.csv"
    source.write_text("Pin\nA1\n", encoding="utf-8")
    assert csv_to_md.csv_to_md(source).splitlines()[0] == "# pin assignments"


def test_csv_to_md_names_the_row_wider_than_the_header(tmp_path: Path) -> None:
    source = tmp_path / "pins.csv"
    source.write_text("Pin,Ball\nA1,B2\nA2,B3,extra\n", encoding="utf-8")
    with pytest.raises(
        ValueError, match=r"row 3 has 3 cells but the header has 2; refusing to discard data$"
    ):
        csv_to_md.csv_to_md(source)


def test_csv_to_md_converts_every_csv_file_of_a_folder_outside_git(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "tables"
    for name in ("kept.csv", "nested/deeper/also kept.csv"):
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_text("Name\nvalue\n", encoding="utf-8")
    output = tmp_path / "converted"
    nested = output / "nested" / "deeper" / "also kept.md"

    assert main(["csv-to-md", str(source), "--dry-run", "-o", str(output)]) == 0
    assert not output.exists()
    assert capsys.readouterr().out == (
        f"Would write: {output / 'kept.md'}\nWould write: {nested}\n"
    )
    assert main(["csv-to-md", str(source), "-o", str(output)]) == 0
    assert sorted(path.relative_to(output).as_posix() for path in output.rglob("*.md")) == [
        "kept.md",
        "nested/deeper/also kept.md",
    ]


def test_an_unknown_delimiter_name_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["csv-to-md", "table.csv", "--delimiter", "pipe"])
    assert stopped.value.code == 2
    assert "argument --delimiter: invalid choice: 'pipe'" in capsys.readouterr().err


def test_the_delimiter_option_overrides_detection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "signals.csv"
    source.write_text("Name,alias;Value\nx,y;1\n", encoding="utf-8")
    assert main(["csv-to-md", str(source), "--delimiter", "semicolon"]) == 0
    assert capsys.readouterr().out == f"Written: {source.with_suffix('.md')}\n"
    table = source.with_suffix(".md").read_text(encoding="utf-8").splitlines()[4:]
    assert table == ["| Name,alias | Value |", "| --- | --- |", "| x,y | 1 |"]


@pytest.mark.parametrize("command", ["excel-to-md", "excel-to-csv"])
def test_a_workbook_that_expands_too_far_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    source = tmp_path / "workbook.xlsx"
    workbook = openpyxl.Workbook()
    workbook.save(source)
    workbook.close()
    monkeypatch.setattr(packages, "MAX_EXPANDED_SIZE", 0)
    load = mock.Mock(side_effect=AssertionError("the workbook was opened"))
    monkeypatch.setattr(openpyxl, "load_workbook", load)
    with pytest.raises(SystemExit, match=rf"^{command}: error: .*workbook\.xlsx expands to"):
        main([command, str(source)])
    load.assert_not_called()
