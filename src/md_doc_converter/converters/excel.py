"""Convert each worksheet of an Excel workbook to a Markdown page or a CSV file, as
docs/excel-and-csv.md describes.

openpyxl reads the workbook with data_only=True, so a formula's cell holds the value that Excel
saved with it. A worksheet's cells become the rows that csv.writer writes, so that excel-to-md's
page is the one that csv-to-md writes from excel-to-csv's file; markdown/tables.py writes it.
"""

import argparse
import csv
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from md_doc_converter.converters.packages import is_compound_file, too_large
from md_doc_converter.markdown.tables import table_page

if TYPE_CHECKING:
    from openpyxl.worksheet.worksheet import Worksheet


def _cells(sheet: Worksheet) -> list[list[str]]:
    """The text of *sheet*'s cells, row by row, as csv.writer writes them: an empty cell is ""
    and any other value its str(). Trailing empty rows and columns are left out."""
    rows = [
        ["" if value is None else str(value) for value in row]
        for row in sheet.iter_rows(values_only=True)
    ]
    width = max((index + 1 for row in rows for index, cell in enumerate(row) if cell), default=0)
    rows = [row[:width] for row in rows]
    while rows and not any(rows[-1]):
        rows.pop()
    return rows


def worksheets(excel_path: str) -> list[tuple[str, list[list[str]]]]:
    """The name and cells of each worksheet of *excel_path*, refusing a workbook that
    conversion cannot read."""
    import openpyxl
    from openpyxl.utils.exceptions import InvalidFileException

    path = Path(excel_path)
    if not path.exists():
        raise FileNotFoundError(f"file not found: {excel_path}")
    if is_compound_file(path):
        raise ValueError(
            f"{excel_path} is encrypted, or in the Excel 97-2003 format; "
            "convert an unencrypted .xlsx copy"
        )
    try:
        with zipfile.ZipFile(path) as archive:
            problem = too_large(archive.infolist())
    except zipfile.BadZipFile:
        problem = None  # openpyxl names what the file is instead.
    if problem is not None:
        raise ValueError(f"{excel_path} {problem}")

    try:
        workbook = openpyxl.load_workbook(path, data_only=True)
    # openpyxl rejects other file types by name, and a damaged file as a zip archive.
    except (InvalidFileException, zipfile.BadZipFile, KeyError) as error:
        raise ValueError(f"not an Excel workbook: {excel_path} ({error})") from error
    try:
        return [(sheet.title, _cells(sheet)) for sheet in workbook.worksheets]
    finally:
        workbook.close()


def excel_to_csv(excel_path: str, output_dir: str | None = None) -> None:
    out = Path(output_dir) if output_dir else Path(excel_path).parent
    for name, rows in worksheets(excel_path):
        if not rows:
            print(f"Skipped (empty): {name}")
            continue
        out.mkdir(parents=True, exist_ok=True)
        csv_path = out / f"{name}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows(rows)
        print(f"Written: {csv_path}")


def excel_to_md(excel_path: str, output_dir: str | None = None, dry_run: bool = False) -> None:
    workbook = Path(excel_path)
    out = Path(output_dir) if output_dir else workbook.parent
    for name, rows in worksheets(excel_path):
        if not rows:
            print(f"Skipped (empty): {name}")
            continue
        page = table_page(
            rows,
            stem=name,
            source=f"the {name} worksheet of {workbook.name}",
            command="excel-to-md",
        )
        md_path = out / f"{name}.md"
        if dry_run:
            print(f"Would write: {md_path}")
            continue
        out.mkdir(parents=True, exist_ok=True)
        md_path.write_text(page, encoding="utf-8", newline="\n")
        print(f"Written: {md_path}")


def _parser(command: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"md-doc-converter {command}", description=description)
    parser.add_argument(
        "excel_file", metavar="WORKBOOK", help="Excel workbook (.xlsx), which is not changed"
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        metavar="DIR",
        help="Folder to write in (default: the workbook's folder)",
    )
    return parser


def markdown_main(argv: list[str] | None = None) -> None:
    parser = _parser(
        "excel-to-md", "Convert each worksheet of an Excel workbook to a Markdown page"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written without creating files",
    )
    args = parser.parse_args(argv)
    try:
        excel_to_md(args.excel_file, args.output_dir, args.dry_run)
    except (OSError, ValueError) as error:
        sys.exit(f"excel-to-md: error: {error}")


def csv_main(argv: list[str] | None = None) -> None:
    parser = _parser("excel-to-csv", "Write each worksheet of an Excel workbook as a CSV file")
    args = parser.parse_args(argv)
    try:
        excel_to_csv(args.excel_file, args.output_dir)
    except (OSError, ValueError) as error:
        sys.exit(f"excel-to-csv: error: {error}")
