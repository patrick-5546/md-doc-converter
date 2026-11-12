"""Convert each worksheet of an Excel workbook to a Markdown page or a CSV file, as
docs/excel-and-csv.md describes.

openpyxl reads the workbook with data_only=True, so a formula's cell holds the value that Excel
saved with it. Each cell is written as Excel shows it, in a column wide enough for it; the
worksheet's cells become the rows that csv.writer writes, so that excel-to-md's page is the one
that csv-to-md writes from excel-to-csv's file; markdown/tables.py writes it.
"""

import argparse
import csv
import datetime
import ntpath
import re
import sys
import zipfile
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from typing import TYPE_CHECKING

from md_doc_converter.converters.packages import is_compound_file, too_large
from md_doc_converter.markdown.tables import table_page, trim_empty_edges

if TYPE_CHECKING:
    from openpyxl.cell.cell import Cell, MergedCell
    from openpyxl.worksheet.worksheet import Worksheet

# Excel's General format shows a number in at most 11 characters, besides a minus sign.
_GENERAL_WIDTH = 11
# The number formats written as Excel shows them: digits with or without a thousands
# separator, decimals, and a percent sign or an exponent. Others are written as General.
_DIGITS = re.compile(r"(?P<group>#,##)?0(?:\.(?P<decimals>0+))?(?:(?P<percent>%)|E\+00)?")
# The color or condition of a format section, such as [Red], and a locale, such as [$-409].
_BRACKETS = re.compile(r"\[[^\]]*\]")
# What Windows and Azure DevOps wikis do not allow in a file name.
_UNSAFE = re.compile(r'[<>:"/\\|?*#\x00-\x1f\x7f-\x9f]')


def _general(value: Decimal) -> str:
    """*value* as Excel's General format shows it: as written when it fits in 11 characters,
    rounded to fit when it has decimals, and otherwise as a number like 1.23457E+11."""
    if not value:
        return "0"
    sign, number = ("-" if value < 0 else ""), abs(value)
    exponent = number.adjusted()
    with localcontext() as context:
        context.rounding = ROUND_HALF_UP
        if -5 < exponent < _GENERAL_WIDTH:
            # The point and the digits before it, at least the 0 of 0.5, leave the decimals.
            decimals = max(_GENERAL_WIDTH - max(exponent + 1, 1) - 1, 0)
            text = format(number, f".{decimals}f")
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            if text != "0" and len(text) <= _GENERAL_WIDTH:
                return sign + text
        mantissa, power = format(number, ".5E").split("E")
    if "." in mantissa:
        mantissa = mantissa.rstrip("0").rstrip(".")
    return f"{sign}{mantissa}E{int(power):+03d}"


def _formatted(value: Decimal, number_format: str) -> str:
    """*value* as Excel shows it in the number format *number_format*."""
    sections = number_format.split(";")
    # A format's first section is for every number when it is alone, else for positive numbers,
    # and for zero unless a third section is. Excel shows a negative number with the second
    # section, which may drop its sign, and zero with the third; those are written as General.
    if (value < 0 and len(sections) > 1) or (value == 0 and len(sections) > 2):
        return _general(value)
    match = _DIGITS.fullmatch(_BRACKETS.sub("", sections[0]).strip())
    if match is None:
        return _general(value)
    decimals = len(match["decimals"] or "")
    with localcontext() as context:
        context.rounding = ROUND_HALF_UP
        if match["percent"]:
            return format(value * 100, f"{',' if match['group'] else ''}.{decimals}f") + "%"
        if match[0].endswith("E+00"):
            mantissa, power = format(value, f".{decimals}E").split("E")
            return f"{mantissa}E{int(power):+03d}"
        return format(value, f"{',' if match['group'] else ''}.{decimals}f")


def _shown(cell: Cell | MergedCell) -> str:
    """The text of *cell* as Excel shows it: TRUE or FALSE, a number in its number format, a date
    or time as ISO 8601 writes it, and any other value as it is."""
    value = cell.value
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        # The shortest decimal that is the float, which is what Excel rounds.
        return _formatted(Decimal(repr(value)), cell.number_format)
    if isinstance(value, datetime.datetime):
        if value.time() == datetime.time():
            return value.date().isoformat()
        return value.isoformat(" ", "seconds" if value.second else "minutes")
    if isinstance(value, datetime.time):
        return value.isoformat("seconds" if value.second else "minutes")
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value)


def _cells(sheet: Worksheet) -> list[list[str]]:
    """The text of *sheet*'s cells, row by row, as csv.writer writes them: an empty cell is "".
    The empty rows and columns around the cells that hold text are left out."""
    return trim_empty_edges([[_shown(cell) for cell in row] for row in sheet.iter_rows()])


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


def _file_name(title: str, used: set[str]) -> str:
    """A file name for the worksheet *title* that Windows and Azure DevOps wikis accept, without
    the spaces and dots around it, with - for each space in it, as wikis name a page, and _ for
    each character they do not allow, and that is not in *used*, ignoring case: a second Notes is
    Notes-2. A name that Windows keeps for a device, such as CON, gets _ after it, before any
    dot. The name is added to *used*."""
    name = re.sub(r"^[\s.]+|[\s.]+$", "", _UNSAFE.sub("_", title))
    name = re.sub(r"\s", "-", name) or "Sheet"
    stem, dot, rest = name.partition(".")
    # Such a name, such as NUL, would swallow the file on Windows.
    if ntpath.isreserved(stem):
        name = f"{stem}_{dot}{rest}"
    candidate, number = name, 1
    while candidate.casefold() in used:
        number += 1
        candidate = f"{name}-{number}"
    used.add(candidate.casefold())
    return candidate


def excel_to_csv(excel_path: str, output_dir: str | None = None) -> None:
    out = Path(output_dir) if output_dir else Path(excel_path).parent
    used: set[str] = set()
    for title, rows in worksheets(excel_path):
        if not rows:
            print(f"Skipped (empty): {title}")
            continue
        out.mkdir(parents=True, exist_ok=True)
        csv_path = out / f"{_file_name(title, used)}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows(rows)
        print(f"Written: {csv_path}")


def excel_to_md(excel_path: str, output_dir: str | None = None, dry_run: bool = False) -> None:
    workbook = Path(excel_path)
    out = Path(output_dir) if output_dir else workbook.parent
    used: set[str] = set()
    for title, rows in worksheets(excel_path):
        if not rows:
            print(f"Skipped (empty): {title}")
            continue
        page = table_page(
            rows,
            title=title.strip(),
            source=f"the {title.strip()} worksheet of {workbook.name}",
            command="excel-to-md",
        )
        md_path = out / f"{_file_name(title, used)}.md"
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
