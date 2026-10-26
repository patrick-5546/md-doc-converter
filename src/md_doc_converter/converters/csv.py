"""Convert CSV files to Markdown pages, as docs/excel-and-csv.md describes.

A folder's CSV files are those in it and its subfolders whose names end in .csv, in any case,
except entries whose names begin with "." and, as gitignore.py decides, what Git ignores. Each
is read as UTF-8 without its byte-order mark, its delimiter is detected from its header line,
and markdown/tables.py writes its page, keeping each cell literal.
"""

import argparse
import csv
import io
import sys
from pathlib import Path

from md_doc_converter import gitignore
from md_doc_converter.console import Console, activated
from md_doc_converter.markdown.tables import table_page

# The delimiters that detection chooses from, by name; on a tie the first wins.
DELIMITERS = {"comma": ",", "semicolon": ";", "tab": "\t"}


def detect_delimiter(text: str) -> str:
    """The delimiter of CSV *text*: the most frequent of comma, semicolon and tab outside
    quotes in its header line. A comma wins a tie, and is the default."""
    counts = dict.fromkeys(DELIMITERS.values(), 0)
    quoted = False
    for character in text:
        if character == '"':
            quoted = not quoted
        elif not quoted:
            if character in "\r\n":
                break
            if character in counts:
                counts[character] += 1
    return max(counts, key=counts.__getitem__)


def csv_to_md(csv_path: Path, delimiter: str | None = None) -> str:
    """Return the Markdown string for a single CSV file.

    *delimiter* is detected from the header line unless given.
    """
    # Excel's "CSV UTF-8" starts the file with a byte-order mark, which is not data.
    text = csv_path.read_text(encoding="utf-8-sig")
    reader = csv.reader(
        io.StringIO(text, newline=""), delimiter=delimiter or detect_delimiter(text)
    )
    rows = list(reader)
    if not rows:
        return ""
    try:
        return table_page(rows, stem=csv_path.stem, source=csv_path.name, command="csv-to-md")
    except ValueError as error:
        raise ValueError(f"{csv_path}: {error}") from error


def find_csv_files(folder: Path, skip_ignored: bool) -> tuple[list[Path], bool]:
    """The CSV files in *folder* and its subfolders, sorted, and whether skipping what Git
    ignores, with *skip_ignored*, left out a CSV file or a folder. Links to folders are not
    followed."""
    found: list[Path] = []
    left_out = False
    for directory, folders, files in folder.walk():
        folders[:] = [name for name in folders if not name.startswith(".")]
        names = [
            name for name in files if not name.startswith(".") and name.lower().endswith(".csv")
        ]
        ignored = gitignore.git_ignored([*folders, *names], directory) if skip_ignored else set()
        left_out = left_out or bool(ignored)
        folders[:] = [name for name in folders if name not in ignored]
        found.extend(directory / name for name in names if name not in ignored)
    return sorted(found), left_out


def convert(
    input_path: str,
    output_dir: str | None = None,
    dry_run: bool = False,
    delimiter: str | None = None,
    respect_gitignore: bool | None = None,
) -> None:
    src = Path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"not found: {input_path}")

    left_out = False
    if src.is_dir():
        skip_ignored = gitignore.skips_ignored(respect_gitignore, src)
        csv_files, left_out = find_csv_files(src, skip_ignored)
    else:
        csv_files = [src]

    if not csv_files:
        hint = " once Git-ignored files are skipped; --no-respect-gitignore includes them"
        print(f"No CSV files found{hint if left_out else ''}.")
        return

    for csv_path in csv_files:
        md_text = csv_to_md(csv_path, delimiter)
        if not md_text:
            print(f"Skipped (empty): {csv_path}")
            continue

        out = Path(output_dir) if output_dir else csv_path.parent
        if output_dir and src.is_dir():
            out /= csv_path.parent.relative_to(src)
        md_path = out / f"{csv_path.stem}.md"

        if dry_run:
            print(f"Would write: {md_path}")
        else:
            out.mkdir(parents=True, exist_ok=True)
            md_path.write_text(md_text, encoding="utf-8", newline="\n")
            print(f"Written: {md_path}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="md-doc-converter csv-to-md",
        description="Convert CSV files to Markdown tables",
    )
    parser.add_argument(
        "input",
        metavar="PATH",
        help="CSV file, or a folder to search with its subfolders",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        metavar="DIR",
        help="Folder to write in, keeping a folder input's subfolders (default: each CSV's folder)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written without creating files",
    )
    parser.add_argument(
        "--delimiter",
        choices=DELIMITERS,
        help="Field delimiter (default: detected from each file's header line)",
    )
    gitignore.add_option(parser)
    args = parser.parse_args(argv)
    delimiter = DELIMITERS[args.delimiter] if args.delimiter else None

    with activated(Console("csv-to-md")):
        try:
            convert(args.input, args.output_dir, args.dry_run, delimiter, args.respect_gitignore)
        except (OSError, UnicodeError, csv.Error, ValueError) as error:
            sys.exit(f"csv-to-md: error: {error}")
