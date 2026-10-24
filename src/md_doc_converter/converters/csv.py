"""Convert CSV files to Markdown pages, as docs/excel-and-csv.md describes.

A folder's CSV files are those that Git lists, which leaves out Git-ignored ones, or every one
outside a Git work tree. Each is read as UTF-8 without its byte-order mark, its delimiter is
detected from its header line, and markdown/tables.py writes its page, keeping each cell literal.
"""

import argparse
import csv
import io
import subprocess
import sys
from pathlib import Path

from md_doc_converter import executables
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


def convert(
    input_path: str,
    output_dir: str | None = None,
    dry_run: bool = False,
    delimiter: str | None = None,
) -> None:
    src = Path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"not found: {input_path}")

    if src.is_dir():
        # Use git ls-files to respect .gitignore; -z lists each name unquoted, after a NUL.
        try:
            result = subprocess.run(
                [
                    executables.resolve("git"),
                    "ls-files",
                    "-z",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "*.csv",
                ],
                cwd=src,
                capture_output=True,
                text=True,
                check=True,
            )
            csv_files = sorted(src / name for name in result.stdout.split("\0") if name)
        except subprocess.CalledProcessError, FileNotFoundError:
            # Fall back to rglob if git is unavailable
            csv_files = sorted(src.rglob("*.csv"))
    else:
        csv_files = [src]

    if not csv_files:
        print("No CSV files found.")
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
            md_path.write_text(md_text, encoding="utf-8")
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
    args = parser.parse_args(argv)
    delimiter = DELIMITERS[args.delimiter] if args.delimiter else None

    try:
        convert(args.input, args.output_dir, args.dry_run, delimiter)
    except (OSError, UnicodeError, csv.Error, ValueError) as error:
        sys.exit(f"csv-to-md: error: {error}")
